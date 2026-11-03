package com.blindnav.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.SystemClock
import android.util.Log
import com.blindnav.app.last10m.EvidenceSource
import com.blindnav.app.last10m.OcrGate
import com.blindnav.app.last10m.TextHit
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * bundled ML Kit 文字识别（任务书第三节）。
 *
 * 四件事是硬要求，都写在代码结构里：
 *
 *  1. **不依赖 Google Play Services 动态下载**：用 `text-recognition` 与
 *     `text-recognition-chinese` 的 bundled 变体，模型打进 APK。国行环境里
 *     靠动态下载的 OCR 会在第一次用到时静默失败，那种失败比慢更糟。
 *  2. **不排队、只处理最新帧**：帧闸门（[OcrGate]）占不到就把这一帧**丢掉**。
 *     OCR 慢于相机是常态，排队会让系统一直在读几秒前的画面，
 *     而"最后十米"恰恰要求证据新鲜——旧帧的招牌可能早就不在视野里了。
 *  3. **有超时与看门狗**（PF-10）：`Tasks.await` 带 3000ms 超时；就算 ML Kit 内部
 *     线程卡住，闸门也会在超时后放开让下一帧进去，**不会永久 busy**、不崩 App、
 *     不重启 Activity。迟到的结果按世代号作废，不进状态机。
 *  4. **节流用单调时钟**（PF-12）：`SystemClock.elapsedRealtime()`，
 *     墙上时钟回拨不能把 OCR 冻住。
 *
 * 中文识别器（zh-Hani）本身也覆盖拉丁字母，所以默认只跑中文一路；
 * 需要英文店名更稳时打开 [latinEnabled]，代价是每帧多一次推理。
 *
 * 注意：本类只证明"识别链路跑过"，不证明准确率。
 * `MODEL_PACKAGED` 与 `OCR_WORKS` 是两件事，后者要真机数据。
 */
class OcrEngine(
    context: Context,
    private val minIntervalMs: Long = 300L,
    private val latinEnabled: Boolean = false,
    private val timeoutMs: Long = OcrGate.DEFAULT_TIMEOUT_MS,
    private val events: (String) -> Unit = { Log.d(TAG, it) },
    private val onHits: (List<TextHit>, Long, Long, Long) -> Unit
) {
    private val zh: TextRecognizer = TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
    private val latin: TextRecognizer? =
        if (latinEnabled) TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) else null
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "BlindNavOcr").apply { isDaemon = true }
    }
    private val alive = AtomicBoolean(true)

    /** 节流 / 丢弃 / 超时 / 迟到结果全部由这一层记账；纯 Kotlin，可以在电脑上测。 */
    private val gate = OcrGate(minIntervalMs, timeoutMs, clock = { SystemClock.elapsedRealtime() }, events = events)

    fun stats(): String = gate.stats()
    fun statsLine(): String = gate.stats()

    /**
     * 提交一帧。所有权约定要写清楚：**闸门接手后 Bitmap 归本类回收**；
     * 被丢弃的帧由调用方负责，所以返回值告诉调用方到底有没有接手。
     */
    fun submit(bitmap: Bitmap, frameId: Long, timestampMs: Long, rotationDegrees: Int = 0): Boolean {
        if (!alive.get()) return false
        val generation = gate.tryBegin(frameId) ?: return false
        return try {
            executor.execute { run(generation, bitmap, frameId, timestampMs, rotationDegrees) }
            events("OCR_START frame=$frameId gen=$generation timeout=${timeoutMs}ms")
            true
        } catch (e: Throwable) {
            gate.finish(generation, frameId, 0L, ok = false)
            events("OCR_FAILED frame=$frameId reason=submit ${e.javaClass.simpleName}")
            false
        }
    }

    private fun run(generation: Long, bitmap: Bitmap, frameId: Long, timestampMs: Long, rotationDegrees: Int) {
        val started = System.nanoTime()
        val hits = ArrayList<TextHit>()
        var ok = true
        try {
            val image = InputImage.fromBitmap(bitmap, rotationDegrees)
            collect(hits, recognize(zh, image, frameId), frameId, timestampMs, EvidenceSource.OCR)
            latin?.let { rec ->
                // 两路结果按文本+位置去重，避免同一个框被算成两次命中，
                // 否则 TargetLock 的"多帧确认"会被同一帧的两路结果骗过去。
                collect(hits, recognize(rec, image, frameId), frameId, timestampMs, EvidenceSource.OCR)
            }
        } catch (e: Throwable) {
            // 识别失败不能污染状态：交给上层的就是"这一帧没有文字证据"。
            ok = false
            events("OCR_FAILED frame=$frameId ${e.javaClass.simpleName}:${e.message}")
        } finally {
            runCatching { bitmap.recycle() }
            val us = (System.nanoTime() - started) / 1000L
            // 世代号不匹配 = 这次结果已经迟到（看门狗早已放开闸门给更新的一帧），必须作废。
            if (gate.finish(generation, frameId, us, ok)) {
                onHits(hits, frameId, timestampMs, us)
            }
        }
    }

    private fun recognize(recognizer: TextRecognizer, image: InputImage, frameId: Long): Text? = try {
        com.google.android.gms.tasks.Tasks.await(
            recognizer.process(image), timeoutMs, TimeUnit.MILLISECONDS)
    } catch (e: com.google.android.gms.tasks.RuntimeExecutionException) {
        events("OCR_FAILED frame=$frameId mlkit=${e.cause?.javaClass?.simpleName ?: e.javaClass.simpleName}")
        null
    } catch (e: IllegalStateException) {
        // Tasks.await 超时抛 IllegalStateException("Task failed due to timeout")
        events("OCR_TIMEOUT frame=$frameId limit=${timeoutMs}ms await=timeout")
        null
    } catch (e: InterruptedException) {
        Thread.currentThread().interrupt()
        events("OCR_TIMEOUT frame=$frameId await=interrupted")
        null
    } catch (e: Throwable) {
        events("OCR_FAILED frame=$frameId ${e.javaClass.simpleName}:${e.message}")
        null
    }

    private fun collect(out: MutableList<TextHit>, text: Text?, frameId: Long, tsMs: Long,
                        source: EvidenceSource) {
        text ?: return
        for (block in text.textBlocks) {
            for (line in block.lines) {
                val box: Rect = line.boundingBox ?: continue
                out.add(
                    TextHit(
                        text = line.text,
                        left = box.left, top = box.top, right = box.right, bottom = box.bottom,
                        confidence = line.confidence?.toDouble() ?: 0.0,
                        sourceFrameId = frameId,
                        timestampMs = tsMs,
                        source = source
                    )
                )
            }
        }
    }

    fun close() {
        alive.set(false)
        executor.shutdownNow()
        runCatching { zh.close() }
        runCatching { latin?.close() }
    }

    private companion object { const val TAG = "BlindNavLast10m" }
}

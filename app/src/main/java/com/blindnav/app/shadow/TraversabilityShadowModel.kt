package com.blindnav.app.shadow

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * SegFormer-B0(Cityscapes) 手机影子推理（任务书第十一节）。
 *
 * 明确边界：Cityscapes 的 road/sidewalk **不等于** BlindNav 的安全可通行性。
 * 这个模块只产出 coarse traversable mask 供 WorldPath 影子记录，不接任何播报。
 *
 * 模型资产来源（按优先级）：
 *   1. getExternalFilesDir("models")/segformer_cityscapes.onnx  —— adb push 换模型不用重装 APK
 *   2. assets/segformer_cityscapes.onnx
 * 两处都没有时 available=false，reason=MODEL_ASSET_MISSING，**不会**假装在跑。
 * 导出脚本见 tools/android/export_segformer_onnx.py。
 *
 * 预处理用 SegFormer 官方 ImageNet 均值方差；输出 logits 取 argmax。
 * Cityscapes 19 类顺序按 labels.json 写死为常量，同时把整张类别直方图记进日志——
 * 如果导出时顺序被打乱，真机上一看直方图就能发现，不会静默给出错误 mask。
 */
class TraversabilityShadowModel(context: Context) {

    class Output(
        val gridWidth: Int,
        val gridHeight: Int,
        val traversable: BooleanArray,
        val traversableRatio: Double,
        val classHistogram: IntArray,
        val preprocessNanos: Long,
        val inferenceNanos: Long,
        val postprocessNanos: Long,
        val inputSize: Int,
        /** 预处理方式：squash = 非等比压扁，与 HuggingFace SegformerImageProcessor 的 do_resize 一致。 */
        val resizeMode: String,
        /** 输出网格到源图像素的映射就是归一化坐标（无 padding），记录一次供审计。 */
        val sourceWidth: Int,
        val sourceHeight: Int
    ) {
        fun json() = linkedMapOf<String, Any?>(
            "seg_grid" to "${gridWidth}x$gridHeight",
            "seg_input" to inputSize,
            "seg_resize" to resizeMode,
            "seg_source_wh" to "${sourceWidth}x$sourceHeight",
            "seg_traversable_ratio" to "%.4f".format(traversableRatio).toDouble(),
            "seg_class_hist" to classHistogram.joinToString(","),
            "seg_preprocess_us" to preprocessNanos / 1000.0,
            "seg_inference_us" to inferenceNanos / 1000.0,
            "seg_postprocess_us" to postprocessNanos / 1000.0,
            "seg_total_us" to (preprocessNanos + inferenceNanos + postprocessNanos) / 1000.0
        )
    }

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession?
    private val inputName: String
    var unavailableReason: String? = null
        private set
    var modelPath: String? = null
        private set

    /** 从模型 metadata 读的输入边长；读不到才用默认 256。 */
    private val size: Int
    /** 输出 logits 的 [C, H, W]，优先从 session.outputInfo 读；读不到在首帧按数据推断。 */
    private var outClasses: Int = -1
    private var outHeight: Int = -1
    private var outWidth: Int = -1

    init {
        var loaded: OrtSession? = null
        var reason: String? = null
        var path: String? = null
        try {
            val external = java.io.File(context.getExternalFilesDir(null), "models/segformer_cityscapes.onnx")
            val bytes: ByteArray? = when {
                external.isFile -> { path = external.absolutePath; external.readBytes() }
                else -> runCatching {
                    context.assets.open(ASSET).readBytes().also { path = "assets/$ASSET" }
                }.getOrNull()
            }
            if (bytes == null) {
                reason = "MODEL_ASSET_MISSING"
            } else {
                val opts = OrtSession.SessionOptions()
                opts.setIntraOpNumThreads(4)
                loaded = env.createSession(bytes, opts)
            }
        } catch (e: Throwable) {
            runCatching { loaded?.close() }
            loaded = null
            reason = "SESSION_CREATE_FAILED:${e.javaClass.simpleName}:${e.message}"
        }
        session = loaded
        unavailableReason = reason
        modelPath = path
        inputName = session?.inputNames?.firstOrNull() ?: "pixel_values"
        // 优先读 metadata 里的形状，不用 sqrt 猜：实测本模型输出是 [1,19,64,64]（1/4 分辨率），
        // 而 sqrt(logits.size / classes) 只是恰好能对上；换任何非方图/非 1/4 输出就会静默错位。
        var declaredSize = 256
        runCatching {
            val node = session?.inputInfo?.get(inputName)
            val tensorInfo = node?.info as? ai.onnxruntime.TensorInfo
            val shape = tensorInfo?.shape
            if (shape != null && shape.size == 4 && shape[3] > 0) declaredSize = shape[3].toInt()
        }
        runCatching {
            val outputName = session?.outputNames?.firstOrNull()
            val node = if (outputName != null) session?.outputInfo?.get(outputName) else null
            val tensorInfo = node?.info as? ai.onnxruntime.TensorInfo
            val shape = tensorInfo?.shape
            if (shape != null && shape.size == 4 && shape[1] > 0 && shape[2] > 0 && shape[3] > 0) {
                outClasses = shape[1].toInt(); outHeight = shape[2].toInt(); outWidth = shape[3].toInt()
            }
        }
        size = declaredSize
    }

    val available: Boolean get() = session != null

    /** 实际生效的输入边长（来自模型 metadata），供日志与资产核对。 */
    val inputSize: Int get() = size

    /** metadata 声明的输出网格，读不到时为 -1。 */
    fun declaredOutput(): String =
        if (outClasses > 0) "${outClasses}x${outHeight}x$outWidth" else "unknown"

    /**
     * 未旋转的整帧 Bitmap → **非等比压扁**到 size×size → logits → argmax → 可通行 mask。
     *
     * 用压扁而不是 letterbox 的原因：HuggingFace `SegformerImageProcessor` 的 do_resize 就是
     * 直接把图 resize 到目标方形，不加黑边。之前这里做 letterbox（灰边 padding），
     * 而消费侧又按归一化坐标去查 mask，两件事叠在一起会把画面纵向错位十几个百分点，
     * 并且黑边区域的 argmax 类别会被当成真实场景类别统计进去。
     */
    fun detect(bitmap: Bitmap): Output? {
        val sess = session ?: return null
        val t0 = System.nanoTime()
        val scaled = Bitmap.createScaledBitmap(bitmap, size, size, true)
        val px = IntArray(size * size)
        scaled.getPixels(px, 0, size, 0, 0, size, size)
        if (scaled !== bitmap) scaled.recycle()

        val plane = size * size
        val buffer = ByteBuffer.allocateDirect(3 * plane * 4).order(ByteOrder.nativeOrder()).asFloatBuffer()
        for (i in px.indices) {
            val p = px[i]
            buffer.put(i, (((p shr 16 and 255) / 255f) - MEAN[0]) / STD[0])
            buffer.put(plane + i, (((p shr 8 and 255) / 255f) - MEAN[1]) / STD[1])
            buffer.put(2 * plane + i, (((p and 255) / 255f) - MEAN[2]) / STD[2])
        }
        buffer.rewind()
        val t1 = System.nanoTime()

        val tensor = OnnxTensor.createTensor(env, buffer, longArrayOf(1, 3, size.toLong(), size.toLong()))
        val classes: Int
        val gridW: Int
        val gridH: Int
        val logits: FloatArray
        try {
            val results = sess.run(mapOf(inputName to tensor))
            val first = results[0] as OnnxTensor
            val shape = first.info.shape
            results.close()
            if (shape == null || shape.size != 4 || shape[0] != 1L) {
                unavailableReason = "UNEXPECTED_OUTPUT_SHAPE:${shape?.joinToString("x") ?: "null"}"
                return null
            }
            classes = shape[1].toInt(); gridH = shape[2].toInt(); gridW = shape[3].toInt()
            // 与 metadata 声明对不上时记录一次，说明模型被换过或 metadata 不可信。
            if (outClasses in 1..classes && (outHeight != gridH || outWidth != gridW)) {
                unavailableReason = "OUTPUT_SHAPE_MISMATCH:declared=${outClasses}x${outHeight}x$outWidth actual=$classes" +
                    "x${gridH}x$gridW"
            }
            outClasses = classes; outHeight = gridH; outWidth = gridW
            logits = FloatArray(classes * gridH * gridW)
            first.floatBuffer.get(logits)
        } catch (e: Throwable) {
            unavailableReason = "RUN_FAILED:${e.javaClass.simpleName}:${e.message}"
            return null
        } finally {
            tensor.close()
        }
        val t2 = System.nanoTime()

        val outW = minOf(gridW, 64)
        val outH = minOf(gridH, 64)
        val traversable = BooleanArray(outW * outH)
        val hist = IntArray(classes.coerceAtLeast(1))
        var traversableCount = 0
        for (gy in 0 until outH) {
            val srcY = (gy * gridH / outH).coerceIn(0, gridH - 1)
            for (gx in 0 until outW) {
                val srcX = (gx * gridW / outW).coerceIn(0, gridW - 1)
                var best = 0
                var bestValue = -Float.MAX_VALUE
                for (c in 0 until classes) {
                    val v = logits[c * gridH * gridW + srcY * gridW + srcX]
                    if (v > bestValue) { bestValue = v; best = c }
                }
                hist[best] += 1
                if (best == LABEL_ROAD || best == LABEL_SIDEWALK) {
                    traversable[gy * outW + gx] = true
                    traversableCount++
                }
            }
        }
        val t3 = System.nanoTime()
        return Output(
            gridWidth = outW, gridHeight = outH, traversable = traversable,
            traversableRatio = traversableCount.toDouble() / (outW * outH),
            classHistogram = hist,
            preprocessNanos = t1 - t0, inferenceNanos = t2 - t1, postprocessNanos = t3 - t2,
            inputSize = size, resizeMode = "squash", sourceWidth = bitmap.width, sourceHeight = bitmap.height
        )
    }

    fun close() = runCatching { session?.close() }

    companion object {
        const val ASSET = "segformer_cityscapes.onnx"
        private val MEAN = floatArrayOf(0.485f, 0.456f, 0.406f)
        private val STD = floatArrayOf(0.229f, 0.224f, 0.225f)
        /** Cityscapes 19 类 trainId 顺序里的前两类。 */
        const val LABEL_ROAD = 0
        const val LABEL_SIDEWALK = 1
        val LABELS = listOf(
            "road", "sidewalk", "building", "wall", "fence", "pole", "traffic light", "traffic sign",
            "vegetation", "terrain", "sky", "person", "rider", "car", "truck", "bus", "train",
            "motorcycle", "bicycle"
        )
    }
}

/**
 * ARCore Scene Semantics 作为第二个可通行性来源。
 *
 * 1.57 的 SemanticLabel 只有 UNLABELED / ROAD / SIDEWALK / TERRAIN / BUILDING / STRUCTURE /
 * TREE / PERSON / VEHICLE / OBJECT / WATER / SKY，**没有 curb 也没有 crosswalk**，
 * 所以它能补"人行道 vs 车行道"这一刀，补不了台阶与斑马线，不能当 SegFormer 的等价替代。
 * 支持与否必须问 `session.isSemanticModeSupported(ENABLED)`，不支持时本来源直接缺席。
 */
object SemanticMask {

    class Output(
        val gridWidth: Int,
        val gridHeight: Int,
        val traversable: BooleanArray,
        val blocked: BooleanArray,
        val traversableRatio: Double,
        val blockedRatio: Double,
        val sourceWidth: Int,
        val sourceHeight: Int,
        val labelHistogram: IntArray
    ) {
        fun json() = linkedMapOf<String, Any?>(
            "sem_grid" to "${gridWidth}x$gridHeight",
            "sem_src_wh" to "${sourceWidth}x$sourceHeight",
            "sem_traversable_ratio" to "%.4f".format(traversableRatio).toDouble(),
            "sem_blocked_ratio" to "%.4f".format(blockedRatio).toDouble(),
            // 直方图按 label 序号统计：万一 ordinal != 像素值，这张表第一时间暴露，不会静默给错 mask。
            "sem_label_hist" to labelHistogram.joinToString(","),
            // 官方 SIDEWALK 含 associated curbs，**没有独立 curb 边界**，
            // 所以这个来源只能当 coarse traversability，不能用来判断"离路缘还有多远"。
            "sem_curb_boundary_available" to false,
            "sem_crosswalk_available" to false
        )
    }

    /**
     * @param image `frame.acquireSemanticImage()` 的结果：单通道 8-bit 类别 id
     * @param classify 把 8-bit 像素值映射到 SemanticLabel 名的函数（由调用方用 ARCore 枚举提供）
     */
    fun read(image: android.media.Image, classify: (Int) -> String): Output? {
        val w = image.width
        val h = image.height
        val plane = image.planes[0]
        val buffer = plane.buffer
        val outW = 64
        val outH = 64
        val traversable = BooleanArray(outW * outH)
        val blocked = BooleanArray(outW * outH)
        val hist = IntArray(32)
        var tCount = 0
        var bCount = 0
        for (gy in 0 until outH) {
            val srcY = (gy * h / outH).coerceIn(0, h - 1)
            for (gx in 0 until outW) {
                val srcX = (gx * w / outW).coerceIn(0, w - 1)
                val index = srcY * plane.rowStride + srcX * plane.pixelStride
                if (index >= buffer.limit()) continue
                val raw = buffer.get(index).toInt() and 0xFF
                hist[raw.coerceAtMost(hist.size - 1)] += 1
                when (classify(raw)) {
                    "SIDEWALK", "ROAD", "TERRAIN" -> { traversable[gy * outW + gx] = true; tCount++ }
                    "VEHICLE", "PERSON", "OBJECT", "STRUCTURE", "TREE", "BUILDING" ->
                        { blocked[gy * outW + gx] = true; bCount++ }
                }
            }
        }
        return Output(outW, outH, traversable, blocked,
            tCount.toDouble() / (outW * outH), bCount.toDouble() / (outW * outH), w, h, hist)
    }
}

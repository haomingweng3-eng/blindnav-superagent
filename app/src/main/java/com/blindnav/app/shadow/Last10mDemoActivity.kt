package com.blindnav.app.shadow

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import com.blindnav.app.TtsEngine
import com.blindnav.app.last10m.DemoFrame
import com.blindnav.app.last10m.DemoScenario
import com.blindnav.app.last10m.DemoScenarios
import com.blindnav.app.last10m.Last10mDemoRunner
import com.blindnav.app.last10m.MiniJson
import com.blindnav.app.last10m.NeedViewPlanner
import com.blindnav.app.last10m.label

/**
 * 最后十米 Demo（任务书第 16/17 节，`LAST10M_DEMO_MODE`）。
 *
 * 明天上台不该需要笔记本 + adb + 五个隐藏页面。这一屏一键进入，选场景、点「下一帧」，
 * 就能把整条链演一遍：目标 → 扫描 → 文字证据 → 多帧锁定 → 候选门 → 关系判定 →
 * 缺哪个视角 → 门前地面 → 入口确认 / 拒绝确认，全程有语音。
 *
 * 三个场景用的是**真实公开图片**（LSVT 真实街景照片），每一帧的 JSON 是云端 VLM 对这些图片的
 * **真实回答**（今晚跑出来后存在 assets 里，不是手写的期望值）。来源一律标 `REPLAY`：
 * 演示时不能把这些说成"手机实时识别到的"。
 *
 * 驱动逻辑不在这里：`Last10mDemoRunner` → `PerceptionOrchestrator` → `Last10mStateMachine` →
 * `ArrivalAuthority` → `Last10mSpeechPolicy` 就是真机那一条链。
 * 演示没有专用实现，否则明天演过的东西证明不了产品状态。
 */
class Last10mDemoActivity : ComponentActivity() {

    private var image: ImageView? = null
    private var header: TextView? = null
    private var detail: TextView? = null
    private var statusView: TextView? = null
    private var scenarios: List<DemoScenario> = emptyList()
    private var runner: Last10mDemoRunner? = null
    private var scenarioIndex = 0
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private var autoplay: Runnable? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        TtsEngine.init(applicationContext)
        scenarios = runCatching { loadScenarios() }.getOrElse {
            Log.w(TAG, "DEMO_LOAD_FAILED ${it.javaClass.simpleName}:${it.message}")
            emptyList()
        }
        Log.w(TAG, "DEMO_OPEN session=demo scenarios=${scenarios.size} " +
            "frames=${scenarios.sumOf { it.frames.size }}")
        setContentView(buildUi())
        if (scenarios.isNotEmpty()) selectScenario(0)
        else statusView?.text = "没有读到 demo/last10m/scenarios.json —— 打包出问题了"
    }

    private fun loadScenarios(): List<DemoScenario> {
        val text = assets.open(SCENARIOS_ASSET).readBytes().toString(Charsets.UTF_8)
        return DemoScenarios.parse(text)
    }

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 24, 16, 16)
            setBackgroundColor(Color.BLACK)
        }
        root.addView(TextView(this).apply {
            text = "最后十米 Demo · 回放（真实公开图片 + 真实模型回答，来源标 REPLAY）"
            setTextColor(Color.LTGRAY); textSize = 12f
        })
        val scenarioRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        scenarios.forEachIndexed { i, sc ->
            scenarioRow.addView(Button(this).apply {
                text = "S${i + 1}"
                contentDescription = sc.title
                textSize = 12f
                setOnClickListener { selectScenario(i) }
            })
        }
        root.addView(scenarioRow)
        val picture = ImageView(this).apply {
            adjustViewBounds = true
            maxHeight = 420
            scaleType = ImageView.ScaleType.FIT_CENTER
        }
        image = picture
        root.addView(picture)
        val head = TextView(this).apply {
            setTextColor(Color.WHITE); textSize = 17f
            setBackgroundColor(Color.argb(200, 20, 20, 20))
            setPadding(18, 14, 18, 14)
        }
        header = head
        root.addView(head)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(frameButton("下一帧") { stepFrame() })
        row.addView(frameButton("上一帧") { backFrame() })
        row.addView(frameButton("自动播放") { startAutoplay() })
        row.addView(frameButton("重来") { selectScenario(scenarioIndex) })
        root.addView(row)
        val row2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row2.addView(Button(this).apply {
            text = "人工补：门前有台阶"
            contentDescription = "人工声明台阶。来源是 DEBUG_MANUAL，不是模型识别出来的"
            textSize = 12f
            setOnClickListener {
                val r = runner ?: return@setOnClickListener
                r.declareGroundManually(step = true)
                Log.w(TAG, "DEMO_MANUAL_GROUND step=true source=DEBUG_MANUAL")
                speak(Last10mDemoGroundSay.recordedStep())
                renderCurrent()
            }
        })
        root.addView(row2)
        val status = TextView(this).apply { setTextColor(Color.YELLOW); textSize = 13f }
        statusView = status
        root.addView(status)
        val det = TextView(this).apply {
            setTextColor(Color.WHITE); textSize = 11f
            typeface = android.graphics.Typeface.MONOSPACE
            setBackgroundColor(Color.argb(160, 0, 0, 0))
            setPadding(14, 12, 14, 12)
        }
        detail = det
        val scroll = ScrollView(this).apply {
            addView(det, LinearLayout.LayoutParams(-1, -2))
        }
        root.addView(scroll, LinearLayout.LayoutParams(-1, -1, 1f))
        return root
    }

    private fun frameButton(text: String, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        textSize = 12f
        contentDescription = text
        setOnClickListener { onClick() }
    }

    private fun selectScenario(i: Int) {
        val sc = scenarios.getOrNull(i) ?: return
        scenarioIndex = i
        autoplay?.let { handler.removeCallbacks(it) }
        runner = Last10mDemoRunner(sc, clock = { SystemClock.elapsedRealtime() }).also { it.reset() }
        statusView?.text = "场景 ${i + 1}/${scenarios.size}：${sc.title}\n" +
            "目标：${sc.targetName} · ${sc.frames.size} 帧 · ${sc.provenance}"
        detail?.text = ""
        header?.text = "目标：${sc.targetName}\n当前：未开始\n下一步：点「下一帧」"
        showImage(sc.frames.firstOrNull())
        Log.w(TAG, "DEMO_SCENARIO id=${sc.id} frames=${sc.frames.size} target=${sc.targetName}")
    }

    private fun stepFrame() {
        val r = runner ?: return
        val out = r.step()
        if (out == null) { statusView?.text = "这个场景已经放完，点「重来」"; return }
        showImage(r.currentFrame)
        render(out)
        if (out.spoken.isNotBlank()) speak(out.spoken)
    }

    private fun speak(text: String) {
        Log.w(TAG, "DEMO_TTS \"$text\"")
        TtsEngine.speak(text)
    }

    private fun backFrame() {
        val r = runner ?: return
        val out = r.back() ?: return
        showImage(r.currentFrame)
        render(out)
    }

    private fun startAutoplay() {
        autoplay?.let { handler.removeCallbacks(it) }
        val tick = object : Runnable {
            override fun run() {
                val r = runner ?: return
                if (r.finished) return
                stepFrame()
                if (!r.finished) handler.postDelayed(this, AUTOPLAY_MS)
            }
        }
        autoplay = tick
        tick.run()
    }

    private fun renderCurrent() { detail?.text = runner?.last()?.let { describe(it) } ?: "" }

    private fun render(out: Last10mDemoRunner.FrameOutcome) {
        header?.text = out.header
        statusView?.text = "帧 ${out.index + 1}/${runner?.frameCount ?: 0}「${out.label}」 " +
            "来源=${out.source} · ${out.note.take(44)}"
        detail?.text = describe(out)
    }

    private fun describe(out: Last10mDemoRunner.FrameOutcome): String = buildString {
        appendLine("state=${out.state} decision=${out.decision}")
        appendLine("needView=${out.needView} reasons=${out.reasons.joinToString("/")}")
        out.reasons.firstOrNull { it.name != "NONE" }?.let {
            appendLine("  为什么：${NeedViewPlanner.describe(it)}")
        }
        appendLine("doors=${out.doors} active=${out.activeDoor ?: "-"} relation=${out.relationSummary}")
        appendLine("ground=${out.ground} groundSource=${out.groundSource}")
        appendLine("doorType=${out.doorType} button=${out.button}")
        appendLine("speech=\"${out.spoken}\" why=${out.speechWhy}")
        appendLine("chain=${out.trail}")
        out.sceneSummary?.let { appendLine("scene=\"$it\"") }
    }

    private fun showImage(frame: DemoFrame?) {
        val view = image ?: return
        if (frame == null || frame.image.isBlank()) { view.setImageBitmap(null); return }
        val bitmap = runCatching {
            assets.open("$DEMO_DIR/${frame.image}").use { BitmapFactory.decodeStream(it) }
        }.getOrNull() ?: run { view.setImageBitmap(null); return }
        view.setImageBitmap(drawBoxes(bitmap, frame.json))
    }

    /** 把这一帧模型给的候选门画在图上：一眼能核对"框有没有落在门上"。 */
    private fun drawBoxes(bitmap: Bitmap, frameJson: String): Bitmap {
        val copy = bitmap.copy(Bitmap.Config.ARGB_8888, true) ?: return bitmap
        val canvas = Canvas(copy)
        val paint = Paint().apply {
            style = Paint.Style.STROKE; strokeWidth = 6f; color = Color.CYAN
        }
        val textPaint = Paint().apply { color = Color.CYAN; textSize = 30f }
        val root = runCatching { MiniJson.parseObject(frameJson) }.getOrNull()
        val doors = (root?.get("doors") as? List<*>)?.mapNotNull { it as? Map<*, *> } ?: emptyList()
        doors.forEachIndexed { i, d ->
            val box = (d["bbox"] as? List<*>)?.mapNotNull { (it as? Double)?.toInt() }
            if (box == null || box.size < 4) return@forEachIndexed
            val left = box[0] * copy.width / 1000f
            val right = box[2] * copy.width / 1000f
            val top = box[1] * copy.height / 1000f
            val bottom = box[3] * copy.height / 1000f
            canvas.drawRect(left, top, right, bottom, paint)
            canvas.drawText("door_${i + 1}", left + 6, (top - 8).coerceAtLeast(24f), textPaint)
        }
        return copy
    }

    override fun onDestroy() {
        autoplay?.let { handler.removeCallbacks(it) }
        super.onDestroy()
    }

    companion object {
        private const val TAG = "BlindNavLast10m"
        const val DEMO_DIR = "demo/last10m"
        const val SCENARIOS_ASSET = "$DEMO_DIR/scenarios.json"
        const val AUTOPLAY_MS = 2_600L
    }
}

/** 人工补台阶时的回执措辞。只说"已记录 + 可能有台阶"，不说安全。 */
object Last10mDemoGroundSay {
    fun recordedStep(): String = "门前区域已记录，可能有台阶。"
}

package com.blindnav.app

import android.graphics.Bitmap
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.camera2.interop.Camera2Interop
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sqrt
import java.util.concurrent.Executors
import com.blindnav.app.PerceptionHealth.Channel

/** 连续看路：摄像头每帧跑一次端侧检测，走廊被占就即时播报。 */
class CameraActivity : ComponentActivity() {

    private lateinit var previewView: PreviewView
    private lateinit var hint: TextView
    private var detector: ObstacleDetector? = null
    private val executor = Executors.newSingleThreadExecutor()      // 障碍检测：每帧
    private val slowExecutor = Executors.newSingleThreadExecutor()  // 台阶/盲道：低频，别堵住每帧检测
    private val slowBusy = java.util.concurrent.atomic.AtomicBoolean(false)
    private var lastAnalyze = 0L
    private var lastSpoken = ""
    private var lastSpokenAt = 0L
    private var lastClear = 0L
    private var pathDetector: BlindPathDetector? = null
    private var groundDetector: GroundDetector? = null
    private var lastGroundAt = 0L
    private var lastGroundSay = ""
    private var lastGroundSayAt = 0L
    private var stepsAheadUntil = 0L
    private var hazardHoldUntil = 0L
    private var lastGroundNear = false
    // 没有盲道时改成"锁一个方向 + 提示音纠偏"，替代反复念"沿盲道直走"
    @Volatile private var azimuth: Float? = null      // 磁方位：只用来回答"朝哪边"
    @Volatile private var gyroAz: Float? = null       // 陀螺仪相对朝向：走直线用，不受金属干扰
    private var gyroSm: Float? = null                 // 低通滤波后的值，去掉手抖
    private val rot = FloatArray(9)
    private val ang = FloatArray(3)
    private var straightSaidAt = 0L
    // 按步子累加位移：左右摆一步左一步右会自己抵消，只有真的越走越偏才提醒
    private var lastPathAt = 0L
    private var lastPathSay = ""
    private var lastPathSayAt = 0L
    private var lastBendSay = 0L
    private var lastBend = 0
    private val walkingSafety = WalkingSafetyPolicy()
    private val walkingState = WalkingState()
    @Volatile private var walkingNow = false
    @Volatile private var corridorAt = 0L
    @Volatile private var corridorAvailable = false
    @Volatile private var corridorSide = "unknown"
    @Volatile private var corridorCurve = "none"
    @Volatile private var corridorScore = 0f
    @Volatile private var lastCorridorObservation: PathObservation? = null
    private var lastHeadingForCorridor: Float? = null
    private var lastHeadingAtNanos = 0L
    private var lastPathCommand = "KEEP"
    private val approachEvidence = ApproachEvidence()
    private var saidClear = false
    private var lastSeenAt = 0L
    private var personStreak = 0
    private var personLastSeenAt = 0L
    private var personSaidAt = 0L
    private var personSaidUrgent = false
    private var lastBand = ""
    private var lastGroundHitAt = 0L
    private var stairsStreak = 0
    // 镜头对焦距离：贴墙/贴障碍时会自动对到一米以内
    @Volatile private var nearFocusAt = 0L
    private val focusWindow = ArrayDeque<Float>()   // 近 60 次对焦值，用来算基线
    @Volatile private var nearFocusSince = 0L
    private var lastFocusWarn = 0L
    private var lastFocusLog = 0L
    private var sensorManager: SensorManager? = null
    private var sensorListener: SensorEventListener? = null
    private val frameGate = CameraFrameGate()
    private val captureDirection = PhoneDirection()
    @Volatile private var sensorTimestampRealtime = false
    private val health = PerceptionHealth()
    @Volatile private var healthSession = 0L
    private val healthHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val healthWatch = object : Runnable {
        override fun run() {
            if (!cameraActive) return
            checkPerceptionHealth()
            healthHandler.postDelayed(this, 1000)
        }
    }
    val cameraActive: Boolean get() = frameGate.active
    @Volatile
    private var blockingNow = false
    @Volatile
    var suppressPathFlag = false
    private data class CapturedFrame(val bitmap: Bitmap, val stamp: CameraFrameGate.Stamp,
                                     val pose: PhoneDirection.FrameReading?, val quality: FrameQuality.Summary)
    private val frameQuality = FrameQualityMonitor()
    private val recentFrames = RecentFrameSelector<CapturedFrame>()
    private val frameLock = Any()
    @Volatile private var qualityLimited = false
    @Volatile private var latestFrameLimited = true
    @Volatile private var lastFrame: CapturedFrame? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val ticket=intent.getLongExtra("safetyToken",-1L)
        if (!NativeSafety.gate.responseAllowed(ticket)) { finish();return }
        instance = this
        if (intent.getBooleanExtra("simulateNear", false)) {
            // 调试用：假装镜头一直对在很近的地方，验证"贴脸→喊停"这条链
            Thread {
                while (true) {
                    Thread.sleep(2500)
                    nearFocusSince = System.currentTimeMillis() - 1000
                    nearFocusAt = System.currentTimeMillis()
                }
            }.start()
        }
        previewView = PreviewView(this)
        hint = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 18f
            setBackgroundColor(Color.argb(140, 0, 0, 0))
            setPadding(24, 16, 24, 16)
        }
        val root = FrameLayout(this).apply {
            addView(previewView, FrameLayout.LayoutParams(-1, -1))
            addView(hint, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        }
        setContentView(root)
        rootView = root
        // 研究面板：只有挂载了最后十米会话才创建。没有会话时这里完全不动，
        // 生产界面与接入本功能前逐像素一致。
        if (last10mSession != null) {
            panelController.sessionMounted(last10mSession!!.sessionId)
            ensureResearchPanel()
        }

        val sm = getSystemService(SENSOR_SERVICE) as SensorManager
        sensorManager = sm
        val listener = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                if (e.sensor.type == Sensor.TYPE_ROTATION_VECTOR) {
                    SensorManager.getRotationMatrixFromVector(rot, e.values)
                    captureDirection.update(rot, false, e.accuracy, e.timestamp,
                        if (e.values.size==5) e.values[4].takeUnless { it<0 } else null)
                    SensorManager.getOrientation(rot, ang)
                    azimuth = ((Math.toDegrees(ang[0].toDouble()).toFloat()) + 360f) % 360f
                } else if (e.sensor.type == Sensor.TYPE_GAME_ROTATION_VECTOR) {
                    SensorManager.getRotationMatrixFromVector(rot, e.values)
                    SensorManager.getOrientation(rot, ang)
                    val raw = ((Math.toDegrees(ang[0].toDouble()).toFloat()) + 360f) % 360f
                    val prev = gyroSm
                    gyroSm = if (prev == null) raw else {
                        val diff = ((raw - prev + 540f) % 360f) - 180f
                        (prev + diff * 0.15f + 360f) % 360f
                    }
                    gyroAz = gyroSm
                } else if (e.sensor.type == Sensor.TYPE_ACCELEROMETER) {
                    val x = e.values[0].toDouble(); val y = e.values[1].toDouble(); val z = e.values[2].toDouble()
                    walkingNow = walkingState.observeMagnitude(
                        sqrt(x * x + y * y + z * z), android.os.SystemClock.elapsedRealtime())
                }
            }
            override fun onAccuracyChanged(s: Sensor?, a: Int) {
                if (s?.type==Sensor.TYPE_ROTATION_VECTOR) captureDirection.accuracyChanged(false,a)
            }
        }
        sensorListener = listener
        sm.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)?.let {
            sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
        }
        sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let {
            sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_UI)
        }
        sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
        }
        TtsEngine.init(this)
        TtsEngine.speak("看路正在启动，识别还没就绪")
        executor.execute { detector = try { ObstacleDetector(applicationContext) } catch (e: Throwable) {
            if (e !is Exception && e !is LinkageError) throw e
            health.initializationFailed(Channel.OBSTACLE)
            runOnUiThread { checkPerceptionHealth() }
            null
        } }
        executor.execute {
            groundDetector = try { GroundDetector(applicationContext) } catch (e: Throwable) {
                if (e !is Exception && e !is LinkageError) throw e
                health.initializationFailed(Channel.GROUND)
                runOnUiThread { checkPerceptionHealth() }
                null
            }
        }

        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val provider = future.get()
                val preview = Preview.Builder().build()
                    .also { it.surfaceProvider = previewView.surfaceProvider }
                val analysisBuilder = ImageAnalysis.Builder()
                    .setTargetRotation(android.view.Surface.ROTATION_0)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                Camera2Interop.Extender(analysisBuilder).setSessionCaptureCallback(
                    object : CameraCaptureSession.CaptureCallback() {
                        override fun onCaptureCompleted(
                            session: CameraCaptureSession,
                            request: CaptureRequest,
                            result: TotalCaptureResult
                        ) {
                            val d = result.get(CaptureResult.LENS_FOCUS_DISTANCE) ?: return
                            val now = System.currentTimeMillis()
                            if (now - lastFocusLog > 3000) {
                                lastFocusLog = now
                                android.util.Log.d("BlindNavFocus", "diopter=%.2f".format(d))
                            }
                            // 用平时的对焦距离当基线：突然对到比平时近一倍以上，说明面前有东西
                            focusWindow.addLast(d)
                            if (focusWindow.size > 60) focusWindow.removeFirst()
                            val base = focusWindow.sorted().let { it[it.size / 2] }
                            val veryNear = d > 2.5f || (d > 1.0f && d > base * 2.0f)
                            if (veryNear) {
                                if (nearFocusSince == 0L) nearFocusSince = now
                                if (now - nearFocusSince > 900) nearFocusAt = now
                            } else nearFocusSince = 0L
                        }
                    })
                val analysis = analysisBuilder.build()
                analysis.setAnalyzer(executor) { proxy -> analyze(proxy) }
                provider.unbindAll()
                val camera = provider.bindToLifecycle(
                    this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis
                )
                sensorTimestampRealtime = androidx.camera.camera2.interop.Camera2CameraInfo.from(camera.cameraInfo)
                    .getCameraCharacteristic(android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) ==
                    android.hardware.camera2.CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME
            } catch (e: Throwable) {
                // 没相机权限或相机被占用：别闪退，说清楚再退出
                TtsEngine.speak("相机打不开，可能是没给权限或者被别的应用占了", true)
                finish()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(proxy: ImageProxy) {
        val session = healthSession
        try {
            // Sample nano first: a scheduling gap must make the frame older, never younger.
            val receivedAtNanos = System.nanoTime()
            val stamp = frameGate.captureFrame(proxy.imageInfo.timestamp, sensorTimestampRealtime,
                android.os.SystemClock.elapsedRealtimeNanos(), receivedAtNanos) ?: return
            val now = System.currentTimeMillis()
            if (now - lastAnalyze < 150) return
            lastAnalyze = now
            val bmp = proxy.toBitmap()
            val rotated = if (proxy.imageInfo.rotationDegrees != 0)
                rotate(bmp, proxy.imageInfo.rotationDegrees.toFloat()) else bmp
            if (!frameGate.isCurrent(stamp)) return
            val summary = try { measureFrameQuality(rotated) } catch (_: RuntimeException) {
                // An auxiliary metric failure must not starve obstacle or ground inference.
                FrameQuality.Summary(0.0, 0.0, 0.0, true, 0.0)
            }
            // Freeze capture pose with the frame. A later sensor update must never relabel it.
            val pose = if (stamp.timeSource == "sensor_realtime")
                captureDirection.cameraHeadingAt(stamp.sensorTimestampNanos) else null
            // 最后十米钩子：没有活跃会话时这一行完全惰性（null 检查即返回）。
            // 不改 CameraX 配置、不改现有检测链路、不新增第二条帧流；
            // 会话内部自己拷贝 Bitmap，不持有这里的 rotated 对象。
            last10mSession?.let { session ->
                runCatching { session.onFrame(rotated, pose?.degrees?.toDouble(), pose?.upright ?: false) }
            }
            val qualityUpdate = synchronized(frameLock) {
                if (!frameGate.isCurrent(stamp)) return
                val frame = CapturedFrame(rotated.copy(Bitmap.Config.ARGB_8888, false), stamp, pose, summary)
                latestFrameLimited = summary.limited
                lastFrame = frame // Always latest; quality selection is for snapshots only.
                recentFrames.add(frame, stamp.atNanos, stamp.generation, pose?.degrees, summary)
                frameQuality.observe(summary, stamp.atNanos, stamp.generation).also {
                    qualityLimited = it.limited
                    // Invalidate already admitted guidance before any later frame/UI publication.
                    TtsEngine.setCameraGuidanceAllowed(this, !latestFrameLimited && !qualityLimited)
                }
            }
            health.success(Channel.FRAME, session, stamp.atNanos)
            if (summary.limited || qualityUpdate.limited) runOnUiThread {
                if (frameGate.isCurrent(stamp) && (latestFrameLimited || qualityLimited)) {
                    lastPathJson = ""; lastPathSay = ""
                    if (qualityUpdate.warn) {
                        val message = "画面细节不足，方向提示暂停，请停下检查镜头和取景"
                        if (!blockingNow && System.currentTimeMillis() >= hazardHoldUntil) hint.text = message
                        TtsEngine.speakVisual(message, observedAtNanos = stamp.atNanos)
                    }
                }
            }
            detector?.let { d ->
                try {
                    val hits = d.detect(rotated)
                    runOnUiThread { if (frameGate.isCurrent(stamp)) {
                        health.success(Channel.OBSTACLE, session, stamp.atNanos)
                        show(hits, stamp.atNanos)
                    } }
                } catch (e: Throwable) {
                    if (e !is Exception && e !is LinkageError) throw e
                    health.failure(Channel.OBSTACLE, session, e is LinkageError)
                }
            }

            // 台阶/斑马线/门和盲道放到另一个线程，避免它们占着主检测线程造成漏报
            val needGround = now - lastGroundAt > 900 && groundDetector != null
            val needPath = now - lastPathAt > 3000
            if ((needGround || needPath) && slowBusy.compareAndSet(false, true)) {
                if (needGround) lastGroundAt = now
                if (needPath) lastPathAt = now
                val copy = rotated.copy(Bitmap.Config.ARGB_8888, false)
                slowExecutor.execute {
                    try {
                        if (needGround) try {
                            val g = groundDetector?.detect(copy)
                            runOnUiThread { if (frameGate.isCurrent(stamp)) {
                                health.success(Channel.GROUND, session, stamp.atNanos)
                                if (g != null) { lastGroundHitAt = System.currentTimeMillis(); showGround(g, stamp.atNanos) }
                                else if (!summary.limited) {
                                    stairsStreak = 0
                                    lastGroundJson = ""
                                    if (System.currentTimeMillis() - lastGroundHitAt > 5000) lastGroundSay = ""
                                }
                            } }
                        } catch (e: Throwable) {
                            if (e !is Exception && e !is LinkageError) throw e
                            health.failure(Channel.GROUND, session, e is LinkageError)
                        }
                        if (needPath) try {
                            val pd = pathDetector ?: BlindPathDetector(applicationContext)
                                .also { pathDetector = it }
                            val pr = pd.detect(copy)
                            runOnUiThread { if (frameGate.isCurrent(stamp)) {
                                health.success(Channel.PATH, session, stamp.atNanos)
                                if (health.usable(Channel.FRAME, session, System.nanoTime()) &&
                                    health.usable(Channel.PATH, session, System.nanoTime()) &&
                                    health.usable(Channel.OBSTACLE, session, System.nanoTime()) &&
                                    health.usable(Channel.GROUND, session, System.nanoTime())) showPath(pr, stamp.atNanos, summary.limited)
                            } }
                        } catch (e: Throwable) {
                            if (e !is Exception && e !is LinkageError) throw e
                            health.failure(Channel.PATH, session, e is LinkageError)
                        }
                    } finally {
                        slowBusy.set(false)
                    }
                }
            }
        } catch (e: Exception) {
            health.failure(Channel.FRAME, session)
        } finally {
            proxy.close()
        }
    }

    /**
     * 研究面板（任务书第 5/7 节）。只在挂载了最后十米会话时出现，用来回答
     * "站在店门口时系统到底在想什么"：目标、OCR 结果、Target Lock、候选门（含来源）、
     * 关系状态与来源、地面状态与来源、扫描方向及其不可用原因、needView、reasonCodes、
     * 播报裁决结果与采集组状态。核心一句话：**为什么现在还不能确认**。
     *
     * 三个研究按钮 + 一个地面按钮（都不改变任何生产判定行为）：
     *  - 标左门 / 标中门 / 标右门：人工声明一个入口候选，框按**当前帧真实尺寸**换算。
     *    上一版是拿面板自己的宽高算框，结果不管点哪儿都得到同一个框，
     *    真机上永远造不出第二个候选门，邻门冲突那条最该拦住系统的路径根本走不到。
     *  - 门前地面已看：人工声明"门前区域进入画面"，来源固定 DEBUG_MANUAL。
     *    它只能说"已记录"，不能说"安全"（状态显示 OBSERVED_MANUAL）。
     *
     * 生命周期由 [ResearchPanelController] 决定，View 这边只执行命令：
     *  - `onResume` 时若已有会话而面板不在 ⇒ 挂上（覆盖"先开看路再挂载"这条路）；
     *  - 会话卸载 ⇒ 拆除或清空，显示 `LAST10M_SESSION_NOT_ACTIVE`，
     *    绝不把上一段会话的 ARRIVAL 留在屏幕上。
     */
    private var rootView: FrameLayout? = null
    private var researchPanel: TextView? = null
    private var researchHeader: TextView? = null
    private var researchContainer: LinearLayout? = null
    private val panelController = com.blindnav.app.last10m.ResearchPanelController()
    private val researchHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val researchTick = object : Runnable {
        override fun run() {
            val session = last10mSession
            researchHeader?.text = session?.headerText() ?: "最后十米会话未挂载"
            val effect = panelController.onTick(session != null, session?.statusText() ?: "")
            when (effect.command) {
                com.blindnav.app.last10m.ResearchPanelController.Command.SHOW_INACTIVE,
                com.blindnav.app.last10m.ResearchPanelController.Command.DETACH -> {
                    researchPanel?.text = effect.text
                    researchContainer?.findViewWithTag<android.view.View>("l10m-buttons")
                        ?.alpha = 0.3f
                }
                else -> {
                    researchPanel?.text = effect.text
                    researchContainer?.findViewWithTag<android.view.View>("l10m-buttons")?.alpha = 1f
                }
            }
            researchHandler.postDelayed(this, 1000)
        }
    }

    /** onCreate / onResume 共用：确保"该有面板时一定有，不该有时无声让路"。 */
    private fun ensureResearchPanel() {
        val root = rootView ?: return
        val sessionActive = last10mSession != null
        // 控制器决定该不该挂；已经挂着就 KEEP，绝不重复创建第二个面板。
        val effects = panelController.onResume(sessionActive)
        if (!sessionActive && !panelController.attached) {
            researchHandler.removeCallbacks(researchTick)
            return
        }
        if (researchContainer == null) buildResearchPanel(root)
        effects.firstOrNull { it.text.isNotEmpty() }?.let { researchPanel?.text = it.text }
        researchHandler.removeCallbacks(researchTick)
        researchHandler.post(researchTick)
    }

    private fun buildResearchPanel(root: FrameLayout) {
        val headerView = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 17f
            setBackgroundColor(Color.argb(190, 0, 0, 0))
            setPadding(24, 18, 24, 18)
        }
        val statusView = TextView(this).apply {
            visibility = View.GONE   // 详细区默认折叠：老师看三行就够，工程师按「详细」展开

            setTextColor(Color.WHITE)
            textSize = 11f
            typeface = android.graphics.Typeface.MONOSPACE
            setBackgroundColor(Color.argb(170, 0, 0, 0))
            setPadding(20, 16, 20, 16)
            maxWidth = 1200
            isClickable = true
            setOnLongClickListener {
                val session = last10mSession ?: return@setOnLongClickListener true
                session.onGroundEvidenceFromDebugManual()
                researchHandler.post { researchTick.run() }
                true
            }
        }
        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            tag = "l10m-buttons"
        }
        for (position in com.blindnav.app.last10m.DebugDoorPosition.values()) {
            buttons.addView(Button(this).apply {
                // 文案写成完整字面量，方便在 APK 里核对"三分区按钮确实进包了"
                text = when (position) {
                    com.blindnav.app.last10m.DebugDoorPosition.LEFT -> "标左门"
                    com.blindnav.app.last10m.DebugDoorPosition.CENTER -> "标中门"
                    com.blindnav.app.last10m.DebugDoorPosition.RIGHT -> "标右门"
                }
                contentDescription = "人工声明${positionLabel(position)}侧的入口候选（按当前帧尺寸换算）"
                textSize = 11f
                setOnClickListener {
                    val session = last10mSession ?: return@setOnClickListener
                    val ok = session.markDebugDoor(position)
                    android.util.Log.w("BlindNavLast10m",
                        "DEBUG_DOOR_CLICK position=$position accepted=$ok")
                    researchHandler.post { researchTick.run() }
                }
            })
        }
        buttons.addView(Button(this).apply {
            text = "门前地面已看"
            contentDescription = "人工声明门前区域已进入画面，不代表门前安全"
            textSize = 11f
            setOnClickListener {
                val session = last10mSession ?: return@setOnClickListener
                session.onGroundEvidenceFromDebugManual()
                researchHandler.post { researchTick.run() }
            }
        })
        // 第二行：用户主动问「前面有什么」（任务书第 14 节第一目标）+ 详细折叠
        val askRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            tag = "l10m-ask"
        }
        askRow.addView(Button(this).apply {
            text = "前面有什么"
            contentDescription = "用当前画面问一次环境描述，一到两句话"
            textSize = 11f
            setOnClickListener {
                val session = last10mSession ?: return@setOnClickListener
                android.util.Log.w("BlindNavLast10m", "SCENE_QUERY_TAP session=${session.sessionId}")
                session.querySceneNow { answer ->
                    runOnUiThread {
                        android.util.Log.w("BlindNavLast10m",
                            "SCENE_QUERY_ANSWER ${answer?.take(60) ?: "none"}")
                        researchHandler.post { researchTick.run() }
                    }
                }
                researchHandler.postDelayed({ researchTick.run() }, 1_500)
            }
        })
        askRow.addView(Button(this).apply {
            text = "详细"
            contentDescription = "展开或收起调试详情"
            textSize = 11f
            setOnClickListener {
                statusView.visibility =
                    if (statusView.visibility == View.VISIBLE) View.GONE else View.VISIBLE
                android.util.Log.w("BlindNavLast10m", "PANEL_DETAIL visibility=${statusView.visibility}")
            }
        })
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(headerView, LinearLayout.LayoutParams(-2, -2))
            addView(askRow, LinearLayout.LayoutParams(-2, -2))
            addView(buttons, LinearLayout.LayoutParams(-2, -2))
            addView(statusView, LinearLayout.LayoutParams(-2, -2))
        }
        root.addView(column, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.START))
        researchPanel = statusView
        researchHeader = headerView
        researchContainer = column
    }

    private fun positionLabel(p: com.blindnav.app.last10m.DebugDoorPosition): String = when (p) {
        com.blindnav.app.last10m.DebugDoorPosition.LEFT -> "左"
        com.blindnav.app.last10m.DebugDoorPosition.CENTER -> "中"
        com.blindnav.app.last10m.DebugDoorPosition.RIGHT -> "右"
    }

    private fun detachResearchPanel() {
        researchHandler.removeCallbacks(researchTick)
        researchContainer?.let { c -> (c.parent as? FrameLayout)?.removeView(c) }
        researchContainer = null
        researchPanel = null
        researchHeader = null
        panelController.reset()
    }

    private fun checkPerceptionHealth() {
        if (!cameraActive) return
        val update = health.poll(System.nanoTime())
        if (Channel.FRAME in update.unavailable) synchronized(frameLock) { lastFrame = null; recentFrames.reset() }
        if (Channel.FRAME in update.unavailable || Channel.GROUND in update.unavailable) {
            lastGroundJson = ""; stairsStreak = 0; lastGroundSay = ""
        }
        if (update.unavailable.isNotEmpty()) {
            lastPathJson = ""; lastPathSay = ""; lastPathCommand = "KEEP"
            corridorAvailable = false; lastCorridorObservation = null; walkingSafety.reset()
        }
        if (Channel.FRAME in update.unavailable || Channel.OBSTACLE in update.unavailable) blockingNow = true
        update.message?.let { hint.text = it; TtsEngine.speak(it, update.urgent) }
    }

    private fun measureFrameQuality(bitmap: Bitmap): FrameQuality.Summary {
        // Fixed sampling scale bounds both metric cost and size-dependent score changes.
        val small = Bitmap.createScaledBitmap(bitmap, 64, 64, true)
        return try {
            val pixels = IntArray(64 * 64)
            small.getPixels(pixels, 0, 64, 0, 0, 64, 64)
            FrameQuality.measure(pixels, 64, 64)
        } finally { if (small !== bitmap) small.recycle() }
    }

    private fun rotate(src: Bitmap, degrees: Float): Bitmap {
        val m = android.graphics.Matrix().apply { postRotate(degrees) }
        return Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)
    }

    private val spokenAt = HashMap<String, Long>()
    private val spokenBand = HashMap<String, String>()
    private val prevWidth = HashMap<String, Float>()
    private val prevWidthAt = HashMap<String, Long>()
    private val urgentAt = HashMap<String, Long>()

    private fun show(hits: List<ObstacleDetector.Hit>, observedAtNanos: Long = System.nanoTime()) {
        if (!cameraActive) return
        val now = System.currentTimeMillis()
        if (personLastSeenAt > 0 && now - personLastSeenAt > 2000) {
            personStreak = 0; personSaidAt = 0; personSaidUrgent = false; personLastSeenAt = 0
            for (key in listOf("人左", "人右", "人正前")) { prevWidth.remove(key); prevWidthAt.remove(key) }
        }
        val hasPerson = hits.any { it.name == "人" }
        if (hasPerson) { personStreak = (personStreak + 1).coerceAtMost(2); personLastSeenAt = now }
        else personStreak = 0

        val heading = gyroAz ?: azimuth
        var rotation = false
        if (heading != null) {
            val prev = lastHeadingForCorridor
            if (prev != null) {
                val gap = System.nanoTime() - lastHeadingAtNanos
                if (gap in 0L..1_000_000_000L) {
                    rotation = abs(((heading - prev + 540f) % 360f) - 180f) >= 6f
                }
            }
            lastHeadingForCorridor = heading
            lastHeadingAtNanos = System.nanoTime()
        }

        val nearFocus = nearFocusAt > 0L && now >= nearFocusAt && now - nearFocusAt < 1_500L
        val decision = walkingSafety.evaluate(WalkingSafetyPolicy.Input(
            nowMs = now,
            corridorAvailable = corridorAvailable && now - corridorAt < 4_500L,
            corridorSide = corridorSide,
            corridorCurve = corridorCurve,
            corridorScore = corridorScore,
            hits = hits.map { WalkingSafetyPolicy.Hit(it.name, it.band, it.corridorOverlap, it.bottom) },
            nearFocus = nearFocus,
            nearFocusSinceMs = nearFocusSince,
            walking = walkingNow,
            rotationSuspected = rotation
        ))
        blockingNow = decision.risk == "STOP"
        if (decision.risk == "STOP") {
            hazardHoldUntil = maxOf(hazardHoldUntil, now + if (hasPerson) 2500 else 1800)
        }
        if (decision.risk == "WARN" && hits.isEmpty() && now - lastFocusWarn > 8000) {
            lastFocusWarn = now
            hazardHoldUntil = maxOf(hazardHoldUntil, now + 1_200)
            TtsEngine.speakVisual("画面很近，请停下用盲杖确认通路", observedAtNanos = observedAtNanos)
        }

        if (hits.isEmpty()) {
            if (now - lastSeenAt > 3000) {
                spokenAt.clear(); spokenBand.clear(); prevWidth.clear(); prevWidthAt.clear(); urgentAt.clear(); approachEvidence.reset()
            }
            saidClear = true
            if (!qualityLimited && !latestFrameLimited && now - lastClear > 8000) { hint.text = "本帧未检出已知障碍，仍需盲杖确认"; lastClear = now }
            applyPathDecision(decision, observedAtNanos)
            return
        }
        lastSeenAt = now
        hint.text = hits.joinToString("，") { "前方${it.side}有${it.name}" }
        if (decision.risk == "CLEAR") { applyPathDecision(decision, observedAtNanos); return }

        val notices = ArrayList<Pair<String, Boolean>>()
        var personUrgentNotice = false
        for (hit in hits) {
            val key = hit.name + hit.side
            val closer = hit.band == "近" && spokenBand[key] != "近"
            val where = if (hit.side == "正前") "正前方有" else "前方${hit.side}有"
            val pw = prevWidth[key]
            val pt = prevWidthAt[key] ?: now
            val approach = approachEvidence.observe(key, hit.widthRatio, hit.band, now)
            val rushing = approach.approaching && (hit.band == "近" || hit.band == "中")
            if (pw == null || now - pt >= 1000 || hit.widthRatio < pw * 0.8f) {
                prevWidth[key] = hit.widthRatio
                prevWidthAt[key] = now
            }
            if (hit.name == "人") {
                if (personStreak < 2 && hit.band != "近" && !rushing) continue
                val urgent = hit.band == "近" || rushing
                if (personSaidAt == 0L || (urgent && !personSaidUrgent)) {
                    notices.add("画面中间有人，请先停下确认通路" to urgent)
                    personUrgentNotice = urgent
                    personSaidAt = now; personSaidUrgent = urgent
                }
                continue
            }
            if (spokenAt.containsKey(key) && !closer && !rushing) continue
            if (rushing && now - (urgentAt[key] ?: 0L) > 4000) {
                urgentAt[key] = now
                notices.add(("停，" + (if (hit.side == "正前") "正前方" else "前方${hit.side}") + "的${hit.name}很近了") to true)
            } else {
                if (closer || !spokenAt.containsKey(key))
                    notices.add((where + hit.name + "，先停下，用盲杖确认通路") to (hit.band == "近"))
            }
            spokenAt[key] = now
            spokenBand[key] = hit.band
        }
        if (personUrgentNotice) {
            val missing = hits.filter { it.name != "人" && notices.none { n -> n.first.contains(it.name) } }
            if (missing.isNotEmpty()) notices.add(0,
                ("画面中还有" + missing.map { it.name }.distinct().joinToString("、") + "，请先停下确认通路") to true)
        }
        if (notices.isNotEmpty()) TtsEngine.speakVisual(notices.joinToString("。") { it.first }, notices.any { it.second }, observedAtNanos, hazard = true)
        applyPathDecision(decision, observedAtNanos)
    }

    /** Path speech is gated by the same per-frame corridor decision as obstacles. */
    private fun applyPathDecision(decision: WalkingSafetyPolicy.Decision, observedAtNanos: Long) {
        val now = System.currentTimeMillis()
        if (blockingNow || suppressPathFlag || now < stepsAheadUntil || now < hazardHoldUntil) return
        if (decision.pathCommand == "KEEP") {
            lastPathCommand = "KEEP"; lastPathSay = ""; return
        }
        if (decision.pathCommand != "LEFT" && decision.pathCommand != "RIGHT") return
        val observation = lastCorridorObservation ?: return
        if (!observation.qualified || observation.crosswalk) return
        if (decision.pathCommand == lastPathCommand && now - lastPathSayAt < 4000L) return
        val msg = observation.text()
        if (msg == lastPathSay) return
        lastPathJson = observation.json(decision.stableFrames)
        hint.text = msg
        TtsEngine.speakVisual(msg, observedAtNanos = observedAtNanos, cameraGuidance = true)
        lastPathSay = msg
        lastPathSayAt = now
        lastPathCommand = decision.pathCommand
    }

    private fun showGround(hit: GroundDetector.Hit, observedAtNanos: Long = System.nanoTime()) {
        if (!cameraActive) return
        val now = System.currentTimeMillis()
        // 台阶要连续两帧都看到才算，避免平路误报
        if (hit.name == "台阶") {
            stairsStreak++
            // 已经到脚前的台阶不再等第二帧，晚一秒就可能踩空
            if (stairsStreak < 2 && !hit.near) return
        } else stairsStreak = 0
        // 去重只抑制重复播报；持续看到台阶时，危险抑制不能过期。
        if (hit.name == "台阶") stepsAheadUntil = now + 10000
        if (hit.name == "树" || hit.name == "垃圾桶") hazardHoldUntil = maxOf(hazardHoldUntil, now + 4000)
        val key = hit.name + hit.side
        if (key == lastGroundSay && !(hit.near && !lastGroundNear)) return
        val out = when (hit.name) {
            "台阶" -> if (hit.near) "前方有台阶，停下，用盲杖探一下再走"
                      else "前面有台阶，慢一点，到了用盲杖探一下"
            "斑马线" -> "前方是斑马线，请停在路边确认过街条件"
            "门" -> "前方有门"
            "树", "垃圾桶" -> (if (hit.side == "正前") "画面中间" else "画面${hit.side}侧") +
                "疑似有" + (if (hit.name == "树") "树" else "垃圾或垃圾桶") + "，请先停下，用盲杖确认通路"
            else -> return
        }
        lastGroundSay = key
        lastGroundNear = hit.near
        lastGroundSayAt = now
        lastGroundJson = "{\"name\":\"" + hit.name + "\",\"t\":" + now + "}" 
        // 看到台阶的十秒内不要再念"沿盲道直走"
        if (hit.name == "台阶") stepsAheadUntil = now + 10000
        TtsEngine.speakVisual(out, hit.name in setOf("台阶", "树", "垃圾桶") && hit.near, observedAtNanos, hazard = true)
    }

    private fun showPath(pr: BlindPathDetector.Result, observedAtNanos: Long = System.nanoTime(), sourceLimited: Boolean = false) {
        if (!cameraActive) return
        val observation = PathObservation.from(pr.onPath, pr.offset, pr.score, pr.crosswalk, pr.crosswalkScore, pr.bend)
        if (sourceLimited || qualityLimited || latestFrameLimited) {
            lastPathJson = ""
            lastPathSay = ""
            lastPathCommand = "KEEP"
            corridorAvailable = false
            lastCorridorObservation = null
            if (observation.crosswalk) {
                val message = observation.text()
                if (message != lastPathSay) TtsEngine.speakVisual(message, observedAtNanos = observedAtNanos, hazard = true)
                lastPathSay = message
            }
            return
        }
        corridorAt = System.currentTimeMillis()
        corridorSide = observation.pathSide
        corridorCurve = observation.curve
        corridorScore = observation.score
        corridorAvailable = observation.qualified && !observation.crosswalk
        lastCorridorObservation = observation
        if (!observation.qualified) {
            lastPathJson = ""; lastBend = 0; return
        }
        if (observation.crosswalk) {
            lastPathJson = observation.json(0)
            val message = observation.text()
            if (message != lastPathSay) {
                TtsEngine.speakVisual(message, observedAtNanos = observedAtNanos, hazard = true)
                lastPathSay = message
            }
            corridorAvailable = false
            return
        }
        lastBend = when (observation.curve) { "left" -> -1; "right" -> 1; else -> 0 }
        lastPathJson = observation.json(0)
        // Direction speech is emitted from show() so it shares obstacle risk and per-frame dwell.
    }

    fun pathState(): String {
        val session = healthSession
        val now = System.nanoTime()
        return if (cameraActive && !qualityLimited && !latestFrameLimited && Channel.entries.all { health.usable(it, session, now) }) lastPathJson else ""
    }

    fun groundState(): String {
        val session = healthSession
        val now = System.nanoTime()
        return if (cameraActive && health.usable(Channel.FRAME, session, now) &&
            health.usable(Channel.GROUND, session, now)) lastGroundJson else ""
    }

    fun allowsDirectionCue(): Boolean {
        val now=System.nanoTime()
        return cameraActive && !qualityLimited && !latestFrameLimited && !blockingNow && System.currentTimeMillis()>=hazardHoldUntil &&
            listOf(Channel.FRAME,Channel.OBSTACLE,Channel.GROUND,Channel.PATH).all { health.usable(it,healthSession,now) }
    }

    fun grabJpeg(maxW: Int = 768): String? {
        val frame = snapshotFrame() ?: return null
        return encodeFrame(frame,maxW)
    }

    private fun snapshotFrame(): CapturedFrame? = synchronized(frameLock) {
        val latest = lastFrame ?: return@synchronized null
        if (!frameGate.isCurrent(latest.stamp)) return@synchronized null
        recentFrames.select(System.nanoTime(), latest.stamp.generation,
            captureDirection.cameraHeading(android.os.SystemClock.elapsedRealtimeNanos())?.degrees) ?: latest
    }

    private fun resetFrameQuality(releaseGuidance: Boolean = false) = synchronized(frameLock) {
        lastFrame = null; recentFrames.reset(); frameQuality.reset(); qualityLimited = false; latestFrameLimited = true
        if (releaseGuidance) TtsEngine.endCameraGuidance(this)
    }

    private fun encodeFrame(frame: CapturedFrame, maxW: Int): String? {
        if (!frameGate.isCurrent(frame.stamp) || System.nanoTime()-frame.stamp.atNanos !in 0L..1_500_000_000L) return null
        val bmp = frame.bitmap
        val w = minOf(maxW, bmp.width)
        val h = (bmp.height.toFloat() * w / bmp.width).toInt()
        val small = Bitmap.createScaledBitmap(bmp, w, h, true)
        val out = java.io.ByteArrayOutputStream()
        small.compress(Bitmap.CompressFormat.JPEG, 80, out)
        if (!frameGate.isCurrent(frame.stamp)) return null
        return android.util.Base64.encodeToString(out.toByteArray(), android.util.Base64.NO_WRAP)
    }

    fun grabJpegPacket(maxW: Int = 768): String? {
        val frame=snapshotFrame() ?: return null
        val base64=encodeFrame(frame,maxW) ?: return null
        val age=System.nanoTime()-frame.stamp.atNanos
        if (!frameGate.isCurrent(frame.stamp) || age !in 0L..3_000_000_000L) return null
        val pose=frame.pose
        return org.json.JSONObject().put("base64",base64).put("ageMs",(age+999_999L)/1_000_000L)
            .put("frameId","${frame.stamp.generation}:${frame.stamp.atNanos}")
            .put("timeSource",frame.stamp.timeSource)
            .put("sensorTimestampNanos",frame.stamp.sensorTimestampNanos.toString())
            .put("captureHeading",pose?.degrees ?: org.json.JSONObject.NULL)
            .put("captureUpright",pose?.upright ?: false)
            .put("poseReference",if(pose!=null) "camera_magnetic_enu" else "unknown")
            .put("poseSkewMs",pose?.skewMs ?: org.json.JSONObject.NULL)
            .put("poseSpreadDegrees",pose?.spreadDegrees ?: org.json.JSONObject.NULL).toString()
    }

    companion object {
        @Volatile
        var instance: CameraActivity? = null

        /**
         * 最后十米会话挂载点。为 null 时相机链路行为与接入本功能前完全一致；
         * 由研究/调试入口显式挂载，不默认开启，也不改动 CameraX 的任何配置。
         */
        @Volatile
        var last10mSession: Last10mSession? = null

        /** 最新一次盲道判断，给对话层用。 */
        @Volatile
        var lastPathJson: String = ""
        /** 最新一次地面检测结果（台阶/斑马线/门），给对话层联动用。 */
        @Volatile
        var lastGroundJson: String = ""

        fun setSuppressPath(v: Boolean) { instance?.suppressPathFlag = v }
    }

    private fun resetWarningsForSession() {
        personStreak = 0; personLastSeenAt = 0; personSaidAt = 0; personSaidUrgent = false
        spokenAt.clear(); spokenBand.clear(); prevWidth.clear(); prevWidthAt.clear(); urgentAt.clear()
        lastGroundSay = ""; lastGroundNear = false; stairsStreak = 0
        lastPathSay = ""; lastBend = 0; lastSeenAt = 0; lastClear = 0; lastPathCommand = "KEEP"
        walkingSafety.reset(); walkingState.reset(); walkingNow = false
        corridorAvailable = false; corridorSide = "unknown"; corridorCurve = "none"
        corridorScore = 0f; corridorAt = 0L; lastCorridorObservation = null
        lastHeadingForCorridor = null; lastHeadingAtNanos = 0L
        approachEvidence.reset()
        nearFocusAt = 0L; nearFocusSince = 0L; focusWindow.clear()
        stepsAheadUntil = 0; hazardHoldUntil = 0; blockingNow = false
    }

    override fun onResume() {
        super.onResume()
        // PF-09：面板不能再只在 onCreate 挂一次。"先开看路 → 回影子屏点挂载 → 回来"
        // 这条路必须真的有面板，否则现场只会看到"点了没反应"。
        val session = last10mSession
        if (session != null) {
            if (panelController.sessionId != session.sessionId) {
                panelController.sessionMounted(session.sessionId)
            }
            ensureResearchPanel()
        } else if (panelController.attached) {
            panelController.sessionUnmounted()
            researchPanel?.text = com.blindnav.app.last10m.ResearchPanelController.NOT_ACTIVE
        }
    }

    override fun onStart() {
        super.onStart()
        resetWarningsForSession()
        resetFrameQuality()
        if (instance === this) TtsEngine.beginCameraGuidance(this)
        captureDirection.clearHistory()
        frameGate.start()
        healthSession = health.start(System.nanoTime())
        healthHandler.post(healthWatch)
    }

    override fun onStop() {
        frameGate.stop()
        captureDirection.clearHistory()
        TtsEngine.invalidateVisual()
        health.stop()
        healthHandler.removeCallbacks(healthWatch)
        resetFrameQuality(releaseGuidance = true)
        lastPathJson = ""
        lastGroundJson = ""
        super.onStop()
    }

    override fun onDestroy() {
        frameGate.stop()
        detachResearchPanel()
        health.stop()
        healthHandler.removeCallbacks(healthWatch)
        if (instance === this) instance = null
        resetFrameQuality(releaseGuidance = true)
        runCatching { sensorManager?.unregisterListener(sensorListener) }
        TtsEngine.speak("相机已关")
        // 在当前推理完成后再关闭模型，避免与工作线程争用已释放的 session。
        executor.execute { detector?.close() }
        slowExecutor.execute { pathDetector?.close(); groundDetector?.close() }
        slowExecutor.shutdown()
        executor.shutdown()
        super.onDestroy()
    }
}

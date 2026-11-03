package com.blindnav.app.shadow

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Session

/**
 * ARCore 影子链路的调试屏（任务书第八节 Phase 3 的 "debug screen"）。
 *
 * 定位：研究/调试入口，不是产品界面。它**不播报任何方向指令**，屏幕上只有数字与状态，
 * 由看得见的工作人员操作；生产 UI（MainActivity 的 WebView）一行未改。
 *
 * 可达性：release 构建里这个 Activity 是 exported=false，只能从 App 内部进入；
 * debug 构建在 src/debug/AndroidManifest.xml 里放开 exported，便于
 * `adb shell am start -n com.blindnav.nav/com.blindnav.app.shadow.ArShadowActivity`
 * 直接拉起做真机测试（见 tools/android/run_ar_probe.sh）。
 */
class ArShadowActivity : ComponentActivity() {

    private lateinit var flags: ShadowFlags
    private var logger: BlindNavSensorLogger? = null
    private var engine: ArShadowEngine? = null
    private lateinit var capture: Last10mCaptureStore
    private lateinit var statusView: TextView
    private lateinit var poiField: EditText
    private lateinit var captureButtons: LinearLayout
    private lateinit var relationButtons: LinearLayout
    private val handler = Handler(Looper.getMainLooper())
    private var lastLocation: Location? = null
    private var installRequested = false

    private val poll = object : Runnable {
        override fun run() {
            render()
            handler.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        flags = ShadowFlags(this)
        capture = Last10mCaptureStore(this)
        setContentView(buildView())
        // applyIntentFlags 必须在 setContentView 之后：它会写 statusView，
        // 而 statusView 是 lateinit，提前调用就是 UninitializedPropertyAccessException 直接崩。
        val autoStart = applyIntentFlags()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 1)
        }
        if (autoStart) {
            // 直接调用，不用 statusView.postDelayed：设备处于 DOZE 时 Activity 只走到 onCreate、
            // 不会 resumed，View 的 run-queue 永不执行，于是"启动"根本没触发、logcat 也是空的。
            // 先跑独立能力探针：它不依赖引擎生命周期，也不会被 onPause→stopEngine 掐断。
            Log.w(TAG, "AUTO_START invoking capability probe directly from onCreate")
            runCapabilityProbe()
        }
    }

    /**
     * 独立 ARCore 能力探针（纯诊断，不接任何算法）。
     *
     * 存在的理由：真机第一轮卡在一个无法回答的问题上——"这台设备到底能不能创建 ARCore Session"。
     * 引擎路径要经过 Activity 生命周期与相机前台权限，在 DOZE/后台限制下会被掐断，
     * 拿不到干净结论。这里只做四件事，全部写 logcat：
     *   1. checkAvailability()（瞬时态走 async 重查，不当失败）
     *   2. new Session(...) 成功/失败 + 异常类名与消息
     *   3. Depth AUTOMATIC / RAW_DEPTH_ONLY、SemanticMode ENABLED 是否支持
     *   4. resume() 能不能拿到相机（失败会明确区分"设备不支持"与"后台无相机权限"）
     * 用完立即 pause()+close()，不留 native 句柄。
     */
    private fun runCapabilityProbe() {
        Thread {
            Log.w(TAG, "PROBE_BEGIN model=${Build.MODEL} device=${Build.DEVICE} product=${Build.PRODUCT} " +
                "manufacturer=${Build.MANUFACTURER} release=${Build.VERSION.RELEASE} sdk=${Build.VERSION.SDK_INT}")
            val availability = runCatching { ArCoreApk.getInstance().checkAvailability(this) }
            var value = availability.getOrNull()
            Log.w(TAG, "PROBE_AVAILABILITY value=$value error=${availability.exceptionOrNull()?.javaClass?.name}")
            if (value != null && value.isTransient) {
                // 官方说明它可能需要联网查询设备兼容性，UNKNOWN_CHECKING 不能当失败。
                val latch = java.util.concurrent.CountDownLatch(1)
                var async: ArCoreApk.Availability? = null
                runCatching {
                    ArCoreApk.getInstance().checkAvailabilityAsync(this) { a ->
                        async = a; latch.countDown()
                    }
                }.onFailure {
                    Log.w(TAG, "PROBE_AVAILABILITY_ASYNC_FAILED error=${it.javaClass.name}:${it.message}")
                    latch.countDown()
                }
                runCatching { latch.await(ASYNC_AVAILABILITY_TIMEOUT_MS, java.util.concurrent.TimeUnit.MILLISECONDS) }
                Log.w(TAG, "PROBE_AVAILABILITY_ASYNC value=$async")
                value = async ?: value
            }
            val created = runCatching { Session(this) }
            val session = created.getOrNull()
            if (session == null) {
                val e = created.exceptionOrNull()
                Log.w(TAG, "PROBE_SESSION_CREATE=FAILED availability=$value " +
                    "exception=${e?.javaClass?.name} message=${e?.message}")
                Log.w(TAG, "PROBE_END verdict=SESSION_FAILED")
                return@Thread
            }
            Log.w(TAG, "PROBE_SESSION_CREATE=SUCCESS availability=$value")
            val depthAuto = runCatching { session.isDepthModeSupported(Config.DepthMode.AUTOMATIC) }
            val depthRaw = runCatching { session.isDepthModeSupported(Config.DepthMode.RAW_DEPTH_ONLY) }
            val semantic = runCatching { session.isSemanticModeSupported(Config.SemanticMode.ENABLED) }
            Log.w(TAG, "PROBE_DEPTH_AUTOMATIC=${depthAuto.getOrNull()} PROBE_DEPTH_RAW_ONLY=${depthRaw.getOrNull()} " +
                "PROBE_SEMANTIC_ENABLED=${semantic.getOrNull()} " +
                "errors=${depthAuto.exceptionOrNull()?.javaClass?.name}/${depthRaw.exceptionOrNull()?.javaClass?.name}/${semantic.exceptionOrNull()?.javaClass?.name}")
            val resumed = runCatching { session.resume() }
            Log.w(TAG, "PROBE_RESUME=${if (resumed.isSuccess) "OK" else "FAILED"} " +
                "exception=${resumed.exceptionOrNull()?.javaClass?.name} message=${resumed.exceptionOrNull()?.message}")
            runCatching { session.pause() }
            runCatching { session.close() }
            Log.w(TAG, "PROBE_END verdict=SESSION_OK depth=${depthAuto.getOrNull()} resume=${resumed.isSuccess}")
        }.also { it.isDaemon = true }.start()
    }

    /**
     * 让 adb 能不开屏点勾选直接配置影子链路，例如：
     *   adb shell am start -n com.blindnav.nav/com.blindnav.app.shadow.ArShadowActivity \
     *     --es shadow_token "$SHADOW_TOKEN" \
     *     --ez ENABLE_ARCORE_OWNER true --ez ENABLE_SENSOR_LOG true --ez ENABLE_WORLDPATH_SHADOW true
     * 只认白名单里的 key，其它 extra 一律忽略。
     *
     * debug 构建里这个 Activity 是 exported=true（否则 adb 起不来），任何 App 都能带 extras 拉起它，
     * 所以必须有一个 token 才接受远程改开关——影子开关会改变相机所有权，不能谁都能翻。
     *
     * @return 是否要求自动启动
     */
    private fun applyIntentFlags(): Boolean {
        val extras = intent?.extras ?: return false
        val token = extras.getString("shadow_token")
        if (token != SHADOW_TOKEN) {
            android.util.Log.w("BlindNavShadow", "intent extras ignored: bad shadow_token")
            return false
        }
        fun bool(key: String, set: (Boolean) -> Unit, get: () -> Boolean) {
            if (extras.containsKey(key)) set(extras.getString(key)?.toBooleanStrictOrNull()
                ?: extras.getBoolean(key, get()))
        }
        bool(ShadowFlags.KEY_ARCORE, { flags.arcoreOwner = it }, { flags.arcoreOwner })
        bool(ShadowFlags.KEY_LOG, { flags.sensorLog = it }, { flags.sensorLog })
        bool(ShadowFlags.KEY_TRAVERSABILITY, { flags.traversabilityExperiment = it }, { flags.traversabilityExperiment })
        bool(ShadowFlags.KEY_WORLDPATH, { flags.worldPathShadow = it }, { flags.worldPathShadow })
        bool(ShadowFlags.KEY_SAFE_MOTION, { flags.safeMotionShadow = it }, { flags.safeMotionShadow })
        bool(ShadowFlags.KEY_LAST10M, { flags.last10mCapture = it }, { flags.last10mCapture })
        if (extras.containsKey(ShadowFlags.KEY_FRAME_SAMPLE)) {
            extras.getString(ShadowFlags.KEY_FRAME_SAMPLE)?.toIntOrNull()?.let { flags.frameSampleEvery = it }
        }
        if (extras.containsKey(ShadowFlags.KEY_SEG_INTERVAL)) {
            extras.getString(ShadowFlags.KEY_SEG_INTERVAL)?.toLongOrNull()?.let { flags.segmentationMinIntervalMs = it }
        }
        if (extras.containsKey(ShadowFlags.KEY_DETECTOR_INTERVAL)) {
            extras.getString(ShadowFlags.KEY_DETECTOR_INTERVAL)?.toLongOrNull()?.let { flags.detectorMinIntervalMs = it }
        }
        // `am start --ez` 存的是 Boolean，而 Bundle.getString() 对 Boolean 键会打警告并返回 null，
        // 所以必须两条都读：先试字符串，再回退 getBoolean。之前只读 getString，
        // 导致 auto_start 永远算 false、能力探针从来没被调用过。
        val autoStart = extras.getString("auto_start")?.toBoolean() == true ||
            extras.getBoolean("auto_start", false)
        Log.w(TAG, "APPLY_INTENT_FLAGS done auto_start=$autoStart token_ok=true")
        return autoStart
    }

    private fun buildView(): View {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        column.addView(TextView(this).apply {
            text = "BlindNav 影子链路调试（不播报）"
            textSize = 20f
        })
        statusView = TextView(this).apply {
            textSize = 13f
            typeface = android.graphics.Typeface.MONOSPACE
            setPadding(0, 12, 0, 12)
        }
        column.addView(statusView)

        val scroll = ScrollView(this)
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.TOP
        }
        body.addView(TextView(this).apply { text = "开关（默认全关）" })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_ARCORE
            isChecked = flags.arcoreOwner
            setOnCheckedChangeListener { _, v -> flags.arcoreOwner = v }
        })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_LOG
            isChecked = flags.sensorLog
            setOnCheckedChangeListener { _, v -> flags.sensorLog = v }
        })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_TRAVERSABILITY
            isChecked = flags.traversabilityExperiment
            setOnCheckedChangeListener { _, v -> flags.traversabilityExperiment = v }
        })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_WORLDPATH
            isChecked = flags.worldPathShadow
            setOnCheckedChangeListener { _, v -> flags.worldPathShadow = v }
        })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_SAFE_MOTION
            isChecked = flags.safeMotionShadow
            setOnCheckedChangeListener { _, v -> flags.safeMotionShadow = v }
        })
        body.addView(CheckBox(this).apply {
            text = ShadowFlags.KEY_LAST10M
            isChecked = flags.last10mCapture
            setOnCheckedChangeListener { _, v -> flags.last10mCapture = v }
        })

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply {
            text = "启动"
            contentDescription = "启动 ARCore 影子链路"
            setOnClickListener { startEngine() }
        })
        row.addView(Button(this).apply {
            text = "停止"
            contentDescription = "停止 ARCore 影子链路"
            setOnClickListener { stopEngine("button") }
        })
        row.addView(Button(this).apply {
            text = "日志目录"
            contentDescription = "显示传感器日志目录"
            setOnClickListener { showLogRoot() }
        })
        body.addView(row)

        body.addView(TextView(this).apply { text = "最后十米 V1（CameraX + bundled OCR，不依赖 ARCore）" })
        val l10Row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        l10Row.addView(Button(this).apply {
            text = "挂载会话"
            contentDescription = "挂载最后十米会话并绑定关系采集器"
            setOnClickListener { mountLast10m() }
        })
        l10Row.addView(Button(this).apply {
            text = "卸载会话"
            contentDescription = "卸载最后十米会话"
            setOnClickListener { unmountLast10m() }
        })
        body.addView(l10Row)
        // Demo 屏：不挂载会话也能单独演（回放走同一套状态机与语音策略）
        l10Row.addView(Button(this).apply {
            text = "Last10m Demo"
            contentDescription = "打开最后十米回放演示，三个真实图片场景"
            setOnClickListener {
                startActivity(android.content.Intent(this@ArShadowActivity,
                    Last10mDemoActivity::class.java))
            }
        })
        // 语音开关放在挂载之前可见的位置：现场要能一眼看到"现在会不会出声"。
        voiceBox = android.widget.CheckBox(this).apply {
            text = "最后十米语音播报（经策略去重，不会每帧都念）"
            isChecked = true
            textSize = 12f
        }
        body.addView(voiceBox)
        body.addView(TextView(this).apply {
            text = "挂载后从正常入口打开\"看路\"，研究面板会出现在相机画面上。" +
                "不从这里启动，是为了不绕过 CameraActivity 的安全令牌校验。"
            textSize = 12f
        })

        body.addView(TextView(this).apply { text = "最后十米关系采集" })
        poiField = EditText(this).apply { hint = "POI 名称" }
        body.addView(poiField)
        val groupRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        groupRow.addView(Button(this).apply {
            text = "开始组"
            setOnClickListener { startGroup() }
        })
        groupRow.addView(Button(this).apply {
            text = "结束组"
            setOnClickListener {
                val dir = capture.finishGroup("manual")
                statusView.text = "已保存 ${dir?.absolutePath ?: "没有进行中的组"}"
            }
        })
        body.addView(groupRow)
        captureButtons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for (kind in Last10mCaptureStore.KINDS) {
            captureButtons.addView(Button(this).apply {
                text = kind
                setOnClickListener { requestCapture(kind) }
            })
        }
        body.addView(captureButtons)
        relationButtons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for (relation in Last10mCaptureStore.RELATIONS) {
            relationButtons.addView(Button(this).apply {
                text = relation
                setOnClickListener {
                    val entry = capture.markRelation(relation)
                    statusView.text = "已标注 $relation -> 第 ${entry?.targetIndex} 张" +
                        if (entry == null) "（没写进去：先点「开始组」，或会话身份与当前组不一致）" else ""
                }
            })
        }
        body.addView(relationButtons)

        scroll.addView(body)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = android.widget.FrameLayout.LayoutParams(-1, -1)
            addView(column, LinearLayout.LayoutParams(-1, -2))
            addView(scroll, LinearLayout.LayoutParams(-1, -1, 1f))
        }
        return content
    }

    /**
     * 启动影子链路。
     *
     * 这一版只加了一件事：**每一条早退路径都必须先写 logcat 再 return**。
     * 之前失败原因只写 statusView，而 render() 每 500ms 会把它冲掉，
     * 结果就是"屏幕上看不到理由、日志里也查不到理由"，真机第一轮卡在这里无法定位。
     * logcat 才是验货依据，statusView 只是给人看的辅助。
     */
    private var last10m: com.blindnav.app.Last10mSession? = null

    /** 语音播报开关。默认开：最后十米必须能被听见（PF-02）。 */
    private var voiceBox: android.widget.CheckBox? = null

    /**
     * 挂载最后十米会话。ARCore 路线暂停后，这条链路只用 CameraX + bundled OCR + 罗盘，
     * 关系数据采集也不再要求 ARCore pose。
     *
     * 语音默认**开**（PF-02）：上一版这里写死 `speakPrompts = false`，
     * 于是最后十米对使用者完全沉默，只剩一块调试面板。现在由
     * `Last10mSpeechPolicy` 负责"什么时候才值得开口"，不是简单地把开关拨到 true。
     */
    private fun mountLast10m() {
        if (last10m != null) { statusView.text = "最后十米会话已挂载"; return }
        val poi = poiField.text.toString().ifBlank { "星巴克咖啡" }
        val profile = com.blindnav.app.last10m.TargetPoiProfile.build(poi)
        val voice = voiceBox?.isChecked ?: true
        val session = com.blindnav.app.Last10mSession(this, profile, speakPrompts = voice)
        session.captureStore = capture
        capture.bindSession(session.sessionId)
        com.blindnav.app.CameraActivity.last10mSession = session
        last10m = session
        statusView.text = "已挂载：${profile.describe()}\n" +
            "session=${session.sessionId} 语音=${if (voice) "开" else "关"}\n" +
            "现在从正常入口打开看路（已开着就先退回桌面再进来一次，面板会挂上）"
        Log.w(TAG, "LAST10M_MOUNT session=${session.sessionId} poi=$poi voice=$voice")
    }

    private fun unmountLast10m() {
        val id = last10m?.sessionId
        last10m?.close()
        last10m = null
        capture.bindSession(null)
        com.blindnav.app.CameraActivity.last10mSession = null
        statusView.text = "已卸载最后十米会话（$id）"
        Log.w(TAG, "LAST10M_UNMOUNT session=${id ?: "-"}")
    }

    private fun startEngine() {
        Log.w(TAG, "SHADOW_START_BEGIN model=${Build.MODEL} device=${Build.DEVICE} " +
            "product=${Build.PRODUCT} release=${Build.VERSION.RELEASE} sdk=${Build.VERSION.SDK_INT} " +
            "manufacturer=${Build.MANUFACTURER}")
        if (engine != null) {
            Log.w(TAG, "SHADOW_START_SKIPPED reason=engine_already_running")
            statusView.text = "已在运行"; return
        }
        val ownership = ArCameraOwnership.probe()
        if (ownership == ArCameraOwnership.State.CAMERA_ACTIVITY_HOLDING) {
            // 不自动 finish CameraActivity：它 onDestroy 会 TtsEngine.speak("相机已关")，
            // 等于在真实用户正在用的导航里插播并中断视觉引导。只提示，由人决定。
            Log.w(TAG, "SHADOW_START_SKIPPED reason=camera_activity_holding")
            statusView.text = "看路相机正在运行。请先在生产界面关闭看路，再点启动（影子不会替你关）"
            return
        }
        if (ownership == ArCameraOwnership.State.SHADOW_RUNNING) {
            Log.w(TAG, "SHADOW_START_SKIPPED reason=shadow_already_running")
            statusView.text = "影子链路已在运行中"
            return
        }
        if (!flags.arcoreOwner) {
            Log.w(TAG, "SHADOW_START_SKIPPED reason=flag_${ShadowFlags.KEY_ARCORE}_false")
            statusView.text = "未勾选 ${ShadowFlags.KEY_ARCORE}"
            return
        }
        queryAvailability { availability -> launchEngine(availability) }
    }

    /**
     * `checkAvailability()` 可能先返回 `UNKNOWN_CHECKING` / `UNKNOWN_TIMED_OUT`，
     * 官方说明它可能需要联网查询设备兼容性。所以瞬时态**不能当失败**：
     * 改用 `checkAvailabilityAsync` 等真实结果，并加一个超时兜底，避免永远卡住。
     */
    private fun queryAvailability(done: (ArCoreApk.Availability) -> Unit) {
        val sync = runCatching { ArCoreApk.getInstance().checkAvailability(this) }
        val first = sync.getOrNull()
        Log.w(TAG, "ARCORE_AVAILABILITY_SYNC value=$first error=${sync.exceptionOrNull()?.javaClass?.name}")
        if (first != null && !first.isTransient) { done(first); return }

        var delivered = false
        val deliver: (ArCoreApk.Availability) -> Unit = { value ->
            if (!delivered) {
                delivered = true
                runOnUiThread { done(value) }
            }
        }
        val started = runCatching {
            ArCoreApk.getInstance().checkAvailabilityAsync(this) { value ->
                Log.w(TAG, "ARCORE_AVAILABILITY_ASYNC value=$value")
                deliver(value ?: ArCoreApk.Availability.UNKNOWN_ERROR)
            }
        }
        if (started.isFailure) {
            Log.w(TAG, "ARCORE_AVAILABILITY_ASYNC_FAILED error=${started.exceptionOrNull()?.javaClass?.name}" +
                ":${started.exceptionOrNull()?.message}")
            deliver(first ?: ArCoreApk.Availability.UNKNOWN_ERROR)
            return
        }
        statusView.postDelayed({
            Log.w(TAG, "ARCORE_AVAILABILITY_ASYNC_TIMEOUT used_fallback=$first")
            deliver(first ?: ArCoreApk.Availability.UNKNOWN_TIMED_OUT)
        }, ASYNC_AVAILABILITY_TIMEOUT_MS)
    }

    private fun launchEngine(availability: ArCoreApk.Availability) {
        Log.w(TAG, "ARCORE_AVAILABILITY_FINAL value=$availability isSupported=${availability.isSupported} " +
            "isTransient=${availability.isTransient} isUnknown=${availability.isUnknown} " +
            "isUnsupported=${availability.isUnsupported}")
        // Availability 没有 isInstalled，"支持但没装/版本太旧"只能按枚举值比对。
        val needsInstall = availability.isSupported &&
            availability != ArCoreApk.Availability.SUPPORTED_INSTALLED
        if (needsInstall) {
            if (installRequested) {
                Log.w(TAG, "ARCORE_SESSION_CREATE=SKIPPED reason=install_already_requested availability=$availability")
                statusView.text = "ARCore 安装未完成，无法启动：$availability"
                return
            }
            installRequested = true
            val status = runCatching { ArCoreApk.getInstance().requestInstall(this, true) }
            Log.w(TAG, "ARCORE_INSTALL_STATUS value=${status.getOrNull()} " +
                "error=${status.exceptionOrNull()?.javaClass?.name}:${status.exceptionOrNull()?.message}")
            statusView.text = "需要安装/更新 Google Play Services for AR（$availability）：${status.getOrNull()}"
            return
        }
        val log = BlindNavSensorLogger(this)
        logger = log
        val (widthPx, heightPx) = viewportSize()
        val newEngine = ArShadowEngine(this, flags, log, displayRotation(), widthPx, heightPx)
        newEngine.captureRequest = { bitmap, pose -> pendingCapture?.invoke(bitmap, pose) }
        engine = newEngine
        val error = newEngine.start()
        Log.w(TAG, "SHADOW_ENGINE_START_RESULT rejected=${error ?: "none"}")
        statusView.text = if (error == null) "启动中…" else "启动被拒绝：$error"
        refreshLocation()
    }

    private fun stopEngine(reason: String) {
        engine?.stop()
        engine = null
        logger?.close(linkedMapOf(
            "closed_reason" to reason,
            "flags" to flags.summary(),
            "last10m_groups" to capture.groupCount()
        ))
        logger = null
        statusView.text = "已停止（$reason），日志已写盘"
    }

    private fun showLogRoot() {
        statusView.text = "日志目录：${BlindNavSensorLogger.logRoot(this).absolutePath}\n" +
            "最后十米：${capture.root.absolutePath}\n" +
            "拉取：adb exec-out run-as com.blindnav.nav ls 或 adb pull <上面的目录>"
    }

    private fun startGroup() {
        refreshLocation()
        val name = capture.startGroup(poiField.text.toString().ifBlank { "unnamed" }, lastLocation)
        statusView.text = if (name == null) "开始组失败" else "已开始 $name"
    }

    private var pendingCapture: ((android.graphics.Bitmap, ArPoseSample) -> Unit)? = null

    private fun requestCapture(kind: String) {
        // ARCore 已暂停：没有引擎时走 CameraX 会话的取帧路径，采集不再需要六自由度位姿。
        val session = last10m
        if (engine == null && session != null) {
            if (capture.activeGroup == null) { statusView.text = "先点\"开始组\""; return }
            val ok = session.capture(kind)
            statusView.text = if (ok) "已存 $kind（CameraX 帧）" else "没有可取的帧，请先打开看路"
            return
        }
        val active = engine
        if (active == null) { statusView.text = "ARCore 未运行且未挂载最后十米会话，无法取帧"; return }
        if (capture.activeGroup == null) { statusView.text = "先点\"开始组\""; return }
        refreshLocation()
        val location = lastLocation
        pendingCapture = { bitmap, pose ->
            val copy = bitmap.copy(Bitmap.Config.ARGB_8888, false)
            val entry = capture.capture(kind, copy, pose, location)
            copy.recycle()
                runOnUiThread { statusView.text = "已存 $kind：${entry?.file}" +
                        if (entry == null) "（写入失败，见 logcat CAPTURE_FAILED）" else "" }
        }
        active.captureRequest = pendingCapture
        statusView.text = "等待下一帧：$kind"
    }

    private fun refreshLocation() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED) return
        val manager = getSystemService(LOCATION_SERVICE) as? LocationManager ?: return
        lastLocation = runCatching {
            listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                .mapNotNull { manager.getLastKnownLocation(it) }
                .maxByOrNull { it.time }
        }.getOrNull()
    }

    /**
     * ARCore 的 `setDisplayGeometry(rotation, widthPx, heightPx)` 直接决定深度/图像到显示空间的
     * 变换，尺寸取错就会得到系统性偏移的几何，所以这里按 API 版本取真实像素尺寸，
     * 不用已废弃的 defaultDisplay 路径（API 30+ 上它拿到的可能不是当前 window 的度量）。
     */
    private fun viewportSize(): Pair<Int, Int> =
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            val bounds = windowManager.currentWindowMetrics.bounds
            Pair(bounds.width(), bounds.height())
        } else {
            @Suppress("DEPRECATION")
            val point = android.graphics.Point().also { windowManager.defaultDisplay.getRealSize(it) }
            Pair(point.x, point.y)
        }

    private fun displayRotation(): Int = window.decorView.display?.rotation ?: Surface.ROTATION_0

    private fun render() {
        val snapshot = engine?.status
        if (snapshot == null) {
            statusView.text = buildString {
                append("state=idle\n")
                append("flags: ").append(flags.summary()).append('\n')
                append("last10m groups=").append(capture.groupCount()).append('\n')
                append("log root=").append(BlindNavSensorLogger.logRoot(this@ArShadowActivity).absolutePath)
            }
            return
        }
        statusView.text = snapshot.entries.joinToString("\n") { "${it.key}=${it.value}" } +
            "\nlast_error=" + (engine?.lastError ?: "-")
    }

    override fun onResume() {
        super.onResume()
        handler.post(poll)
        val (widthPx, heightPx) = viewportSize()
        engine?.updateViewport(displayRotation(), widthPx, heightPx)
    }

    override fun onPause() {
        handler.removeCallbacks(poll)
        // 退到后台必须让出相机：ARCore 不会和别的前台 App 共享后摄。
        stopEngine("paused")
        super.onPause()
    }

    override fun onDestroy() {
        handler.removeCallbacks(poll)
        pendingCapture = null
        super.onDestroy()
    }

    companion object {
        const val TAG = "BlindNavShadow"

        /**
         * adb 远程改开关的口令。debug 包里这个 Activity 是 exported=true，
         * 没有口令就等于任何 App 都能翻转相机所有权并 auto_start。
         * 与 tools/android/run_ar_probe.sh 里的 SHADOW_TOKEN 必须一致。
         */
        const val SHADOW_TOKEN = "blindnav-shadow-2026"

        /** checkAvailabilityAsync 兜底超时：回调不来时用它，避免永远卡住。 */
        const val ASYNC_AVAILABILITY_TIMEOUT_MS = 10_000L
    }
}

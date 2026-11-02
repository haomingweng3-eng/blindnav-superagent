package com.blindnav.app

import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class MainActivity : Activity() {

    private lateinit var web: WebView
    private var recognizer: SpeechRecognizer? = null
    private var detector: BlindPathDetector? = null
    private var obstacleDetector: ObstacleDetector? = null
    private val rotationMatrix = FloatArray(9)
    private val phoneDirection = PhoneDirection()
    @Volatile private var fallArmed = false
    @Volatile private var freeFallAt = 0L
    @Volatile private var lastFallAsk = 0L

    private val accListener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            if (e.sensor.type != Sensor.TYPE_ACCELEROMETER) return
            val x = e.values[0]; val y = e.values[1]; val z = e.values[2]
            val mag = Math.sqrt((x * x + y * y + z * z).toDouble()).toFloat()
            val now = System.currentTimeMillis()
            // 先失重（掉下去），随后一记重撞（落地）
            if (mag < 3.0f) freeFallAt = now
            else if (mag > 24f && freeFallAt > 0 && now - freeFallAt < 1500) {
                freeFallAt = 0
                if (now - lastFallAsk > 120000) {
                    lastFallAsk = now
                    TtsEngine.speak("你是不是摔了？要帮忙就喊救命，没事实在没事就行")
                }
            }
        }
        override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    }

    private val sensorListener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            if (e.sensor.type == Sensor.TYPE_ROTATION_VECTOR ||
                e.sensor.type == Sensor.TYPE_GAME_ROTATION_VECTOR) {
                SensorManager.getRotationMatrixFromVector(rotationMatrix, e.values)
                val relative = e.sensor.type == Sensor.TYPE_GAME_ROTATION_VECTOR
                val error = if (!relative && e.values.size == 5) e.values[4].takeUnless { it < 0 } else null
                phoneDirection.update(rotationMatrix, relative, e.accuracy, e.timestamp, error)
            }
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
            if (sensor?.type == Sensor.TYPE_ROTATION_VECTOR || sensor?.type == Sensor.TYPE_GAME_ROTATION_VECTOR)
                phoneDirection.accuracyChanged(sensor.type == Sensor.TYPE_GAME_ROTATION_VECTOR, accuracy)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 高德 SDK 要求先声明隐私合规，否则地图/导航不可用
        com.amap.api.maps.MapsInitializer.updatePrivacyShow(this, true, true)
        com.amap.api.maps.MapsInitializer.updatePrivacyAgree(this, true)
        com.amap.api.location.AMapLocationClient.updatePrivacyShow(this, true, true)
        com.amap.api.location.AMapLocationClient.updatePrivacyAgree(this, true)
        web = WebView(this)
        WebView.setWebContentsDebuggingEnabled(true)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
            @Suppress("DEPRECATION")
            run {
                allowFileAccessFromFileURLs = true
                allowUniversalAccessFromFileURLs = true
            }
            setGeolocationEnabled(true)
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(
                origin: String?, callback: GeolocationPermissions.Callback?
            ) {
                callback?.invoke(origin, true, false)
            }

            override fun onPermissionRequest(request: PermissionRequest?) {
                request?.grant(request.resources)
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                if (!safetyGate.effectsAllowed()) {
                    view?.evaluateJavascript("window.onNativeSafety&&window.onNativeSafety(${safetyGate.token()},'stop');", null)
                }
                intent.getStringExtra("say")?.let { t ->
                    view?.evaluateJavascript("window.onAsr('${esc(t)}')", null)
                }
                if (intent.getBooleanExtra("selftest", false)) {
                    view?.evaluateJavascript("window.runSelftest&&window.runSelftest()", null)
                }
            }
        }
        web.addJavascriptInterface(Bridge(), "Native")
        web.loadUrl("file:///android_asset/index.html")
        setContentView(web)

        if (intent.getBooleanExtra("dumpmic", false)) {
            web.postDelayed({ runOnUiThread { engine().dumpMic(4) } }, 7000)
        }
        if (intent.getBooleanExtra("playtest", false)) {
            // 外放一段中文，验证"麦克风->识别"这条真实链路
            Thread {
                Thread.sleep(12000)   // 等开场白播完、麦克风打开
                try {
                    val bytes = assets.open("asr/selftest.wav").readBytes()
                    val pcm = java.nio.ByteBuffer.wrap(bytes, 44, bytes.size - 44)
                        .order(java.nio.ByteOrder.LITTLE_ENDIAN).asShortBuffer()
                    val arr = ShortArray(pcm.remaining()); pcm.get(arr)
                    val at = android.media.AudioTrack.Builder()
                        .setAudioAttributes(android.media.AudioAttributes.Builder()
                            .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH).build())
                        .setAudioFormat(android.media.AudioFormat.Builder()
                            .setEncoding(android.media.AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(16000)
                            .setChannelMask(android.media.AudioFormat.CHANNEL_OUT_MONO).build())
                        .setBufferSizeInBytes(arr.size * 2).setTransferMode(android.media.AudioTrack.MODE_STATIC).build()
                    at.write(arr, 0, arr.size)
                    at.play()
                    Thread.sleep(arr.size * 1000L / 16000 + 500)
                    at.release()
                } catch (e: Throwable) { android.util.Log.e("BlindNavPlay", "playtest failed", e) }
            }.start()
        }
        if (intent.getBooleanExtra("asrtest", false)) {
            web.postDelayed({ runOnUiThread { engine().selfTest() } }, 3000)
        }

        TtsEngine.init(this)
        TtsEngine.onStart = { asrEngine?.pause() }
        TtsEngine.onDone = {
            // 等扬声器余音散了再开麦，免得把自己说的话收进去
            Thread {
                Thread.sleep(250)
                TtsEngine.ifIdle {
                    if (dialogOn || singleListenUntilNanos - System.nanoTime() in 1L..15_000_000_000L) asrEngine?.resume()
                }
            }.start()
            js("window.onSpeakDone&&window.onSpeakDone()")
        }

        askNextPermission()

        if (intent.getBooleanExtra("watch", false)) {
            startActivity(Intent(this, CameraActivity::class.java)
                .putExtra("safetyToken", safetyGate.token())
                .putExtra("simulateNear", intent.getBooleanExtra("simulateNear", false)))
        }

        val sm = getSystemService(SENSOR_SERVICE) as SensorManager
        sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let {
            sm.registerListener(sensorListener, it, SensorManager.SENSOR_DELAY_UI)
        }
        sm.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)?.let {
            sm.registerListener(sensorListener, it, SensorManager.SENSOR_DELAY_GAME)
        }
        sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            sm.registerListener(accListener, it, SensorManager.SENSOR_DELAY_GAME)
        }
    }

    override fun onPause() {
        super.onPause()
        pageVisible = false
        singleListenUntilNanos = 0L
        TtsEngine.invalidateVisual()
        // 新相机 Activity 的 onCreate 在此之后，不能用 instance 判断是否需要免提。
        // 已开启的免提由可见的麦克风前台服务维持，界面切换不结束对话。
        if (!dialogOn) runCatching { asrEngine?.stop() }
    }

    override fun onResume() {
        super.onResume()
        pageVisible = true
        // 回到前台如果本该在听，就接着听
        if (dialogOn) runOnUiThread { engine().start() }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        if (intent == null) return
        setIntent(intent)
        intent.getStringExtra("say")?.let { t ->
            runOnUiThread { web.evaluateJavascript("window.onAsr('${esc(t)}')", null) }
        }
        if (intent.getBooleanExtra("selftest", false)) {
            runOnUiThread { web.evaluateJavascript("window.runSelftest&&window.runSelftest()", null) }
        }
    }

    private val perms = arrayOf(
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.CAMERA,
        Manifest.permission.POST_NOTIFICATIONS
    )
    private var permIdx = 0

    private fun askNextPermission() {
        while (permIdx < perms.size &&
            checkSelfPermission(perms[permIdx]) == PackageManager.PERMISSION_GRANTED
        ) permIdx++
        if (permIdx >= perms.size) return
        val hint = when (perms[permIdx]) {
            Manifest.permission.RECORD_AUDIO -> "第一步，请点允许，让我能听到你说话"
            Manifest.permission.ACCESS_FINE_LOCATION -> "第二步，请点允许，让我知道你走到哪了"
            Manifest.permission.CAMERA -> "第三步，请点允许，让我帮你看路"
            else -> "最后一步，请点允许，导航时会有提示通知"
        }
        TtsEngine.speak(hint)
        runCatching { requestPermissions(arrayOf(perms[permIdx]), 100 + permIdx) }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val denied = grantResults.any { it != PackageManager.PERMISSION_GRANTED }
        if (denied) TtsEngine.speak("刚才没允许，我在屏幕上再弹一次，请找到允许按钮")
        permIdx++
        askNextPermission()
        if (perms.all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }) {
            js("window.onPermsReady&&window.onPermsReady()")
        }
    }

    override fun onDestroy() {
        closeAsyncHttp()
        recognizer?.destroy()
        super.onDestroy()
    }

    private fun js(code: String) = runOnUiThread { web.evaluateJavascript(code, null) }

    @Volatile
    private var dialogOn = false
    @Volatile private var singleListenUntilNanos = 0L
    private val listeningGate = ListeningGate()
    @Volatile
    private var asrToken = 0
    @Volatile
    private var asrFailures = 0
    private var asrEngine: AsrEngine? = null
    private val safetyGate = NativeSafety.gate
    @Volatile private var activeHttp: HttpURLConnection? = null

    // Async transport binding
    private val asyncBridgeLock = Any()
    private val asyncBridgeGeneration = java.util.concurrent.atomic.AtomicLong()
    private val asyncTickets = java.util.concurrent.ConcurrentHashMap<String, Pair<Long, Long>>()
    private val asyncClient = lazy {
        AsyncHttpClient(safetyGate::token, safetyGate::responseAllowed, { id, raw ->
            // Do not acquire asyncBridgeLock from the client's delivery lock.
            val ticket = asyncTickets.remove(id)
            if (ticket != null) web.post {
                if (asyncBridgeGeneration.get() == ticket.second && safetyGate.responseAllowed(ticket.first)) {
                    web.evaluateJavascript("window.onHttpResult&&window.onHttpResult(" +
                        org.json.JSONObject.quote(id) + "," + org.json.JSONObject.quote(raw) + ");", null)
                }
            }
        })
    }
    private fun cancelAsyncHttp() {
        synchronized(asyncBridgeLock) {
            asyncBridgeGeneration.incrementAndGet()
            asyncTickets.clear()
            if (asyncClient.isInitialized()) asyncClient.value.cancelAll()
        }
    }
    private fun closeAsyncHttp() {
        synchronized(asyncBridgeLock) {
            asyncBridgeGeneration.incrementAndGet()
            asyncTickets.clear()
            if (asyncClient.isInitialized()) asyncClient.value.close()
        }
    }
    // End async transport binding

    @Volatile private var pageVisible = false

    /** 不依赖网页事件循环的语音控制，云端请求阻塞时也能调速和关麦。 */
    private fun handleImmediateVoiceControl(text: String): Boolean {
        val t = text.trim().replace(Regex("[，,。！!\\s]"), "").removePrefix("小导")
        val rate = when {
            Regex("^(请)?(慢点说|说慢点|慢一点|说慢一点|说慢点儿|太快了|语速慢一点)[啊吧]*$").matches(t) -> 0.72f
            Regex("^(请)?(说快点|快一点|语速快一点)[啊吧]*$").matches(t) -> 1.15f
            Regex("^(正常语速|恢复正常语速|语速正常)[啊吧]*$").matches(t) -> 1f
            else -> null
        }
        if (rate != null) {
            TtsEngine.changeSpeed(rate)
            TtsEngine.speak(if (rate < 1) "好，我慢点说" else if (rate > 1) "好，我说快点" else "好，恢复正常语速", true)
            return true
        }
        if (Regex("^(别听了|关闭监听|关闭语音监听|关掉麦|不用听我说话|先别听)[啊吧]*$").matches(t)) {
            val ticket = listeningGate.stop()
            dialogOn = false
            singleListenUntilNanos = 0L
            runOnUiThread {
                listeningGate.perform(ticket) {
                    runCatching { asrEngine?.stop() }
                    runCatching { stopService(Intent(this, ListenService::class.java)) }
                }
            }
            js("window.onNativeVoiceControl&&window.onNativeVoiceControl('off');")
            TtsEngine.speak("已关闭语音监听", true)
            return true
        }
        return false
    }

    private fun handleImmediateSafetyCommand(text: String): Boolean {
        val kind = SafetyGate.classify(text) ?: return false
        val token = beginVisualCancellation()
        if (kind == "camera") {
            runOnUiThread { CameraActivity.instance?.finish() }
        } else if (kind != "door") {
            NavigationService.cancel()
            stopService(Intent(this, NavigationService::class.java))
            CameraActivity.setSuppressPath(true)
        }
        TtsEngine.interrupt()
        TtsEngine.speak(when (kind) {
            "help" -> "需要帮助，请帮我一下"
            "camera" -> "已关闭看路相机"
            "door" -> "已停止找门，原来的路线继续保留"
            else -> "导航已停止，还能接着说"
        }, true)
        js("window.onNativeSafety&&window.onNativeSafety($token,${org.json.JSONObject.quote(kind)});")
        return true
    }

    private fun beginVisualCancellation(): Long {
        val token = safetyGate.beginSafetyCommand()
        cancelAsyncHttp()
        TtsEngine.invalidateVisual()
        val oldConnection = activeHttp
        Thread { runCatching { oldConnection?.disconnect() } }.start()
        return token
    }

    private fun dispatchUtterance(text: String) {
        // Revoke native deliveries before posting the recognition to the page.
        cancelAsyncHttp()
        js("window.onNativeUtterance&&window.onNativeUtterance(" + org.json.JSONObject.quote(text) + ");")
        if (!handleImmediateVoiceControl(text) && !handleImmediateSafetyCommand(text)) {
            js("window.onAsr(" + org.json.JSONObject.quote(text) + ");")
        }
    }

    private fun engine(): AsrEngine {
        asrEngine?.let { return it }
        val e = AsrEngine(
            this,
            onFinal = { text ->
                dispatchUtterance(text)
                if (!dialogOn && singleListenUntilNanos != 0L) { singleListenUntilNanos = 0L; asrEngine?.stop() }
            },
            onPartial = { text -> js("window.onPartialAsr&&window.onPartialAsr('${esc(text)}');") },
            onInterrupt = {
                runCatching { TtsEngine.interrupt() }
                runCatching { asrEngine?.resume() }
            },
            onNoSpeech = { js("window.onNoSpeech&&window.onNoSpeech();") },
            onMicSilent = {
                // 两个录音源都是静音：多半被系统或别的应用占着
                TtsEngine.speak("麦克风没有声音，看看是不是被别的应用占用了")
            }
        )
        asrEngine = e
        return e
    }

    /** 听一轮；对话模式下听不到就隔一会儿再听。 */
    private fun listenTurn() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            js("window.onAsr('');")
            return
        }
        val token = ++asrToken
        runCatching { recognizer?.destroy() }
        // 国产 ROM 常常没设默认识别服务，显式指定小米自带的那个
        val mi = android.content.ComponentName(
            "com.xiaomi.mibrain.speech", "com.xiaomi.mibrain.speech.asr.AsrService"
        )
        android.util.Log.i("BlindNavASR", "listenTurn dialog=$dialogOn")
        recognizer = runCatching { SpeechRecognizer.createSpeechRecognizer(this, mi) }
            .getOrElse { SpeechRecognizer.createSpeechRecognizer(this) }
        recognizer?.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                if (token != asrToken) return
                asrFailures = 0
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull() ?: ""
                android.util.Log.i("BlindNavASR", "onResults: $text")
                if (text.isNotBlank()) js("window.onAsr('${esc(text)}');")
                scheduleNext()
            }

            override fun onError(error: Int) {
                if (token != asrToken) return
                android.util.Log.w("BlindNavASR", "onError: $error")
                // 9=权限 11=服务断开 13/14=语言不支持：这些是真不可用，其他（没说话/超时）继续听
                val fatal = error == 9 || error == 11 || error == 12 ||
                        error == 13 || error == 14
                if (fatal) {
                    asrFailures++
                    if (asrFailures >= 3) {
                        dialogOn = false
                        js("window.onAsrUnavailable&&window.onAsrUnavailable($error);")
                        return
                    }
                } else {
                    asrFailures = 0
                }
                scheduleNext()
            }
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1200)
        }
        runCatching { recognizer?.startListening(intent) }
    }

    private fun scheduleNext() {
        if (!dialogOn) return
        web.postDelayed({ if (dialogOn) listenTurn() }, 900)
    }

    private fun esc(s: String) = s.replace("\\", "\\\\").replace("'", "\\'")
        .replace("\n", "\\n").replace("\r", "")

    inner class Bridge {

        @JavascriptInterface
        fun httpAsync(id: String, method: String, url: String, headersJson: String, body: String?) {
            synchronized(asyncBridgeLock) {
                val ticket = safetyGate.token()
                if (!safetyGate.responseAllowed(ticket) || !id.matches(Regex("[A-Za-z0-9-]{1,80}"))) return
                if (asyncTickets.putIfAbsent(id, Pair(ticket, asyncBridgeGeneration.get())) != null) return
                asyncClient.value.request(id, method, url, headersJson, body)
            }
        }

        @JavascriptInterface
        fun cancelHttp() { cancelAsyncHttp() }



        /** 通用 HTTP，绕开 CORS：页面不用直接连外网。 */
        @JavascriptInterface
        fun http(method: String, url: String, headersJson: String, body: String?): String {
            val token = safetyGate.token()
            if (!safetyGate.responseAllowed(token)) return """{"error":"cancelled"}"""
            var connection: HttpURLConnection? = null
            return try {
                val conn = URL(url).openConnection() as HttpURLConnection
                connection = conn
                activeHttp = conn
                if (!safetyGate.responseAllowed(token)) return """{"error":"cancelled"}"""
                conn.requestMethod = method
                conn.connectTimeout = 15000
                conn.readTimeout = 60000
                if (!headersJson.isNullOrBlank()) {
                    org.json.JSONObject(headersJson).let { h ->
                        h.keys().forEach { conn.setRequestProperty(it, h.getString(it)) }
                    }
                }
                if (!body.isNullOrBlank()) {
                    conn.doOutput = true
                    conn.outputStream.use { it.write(body.toByteArray()) }
                }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader()?.use(BufferedReader::readText) ?: ""
                if (!safetyGate.responseAllowed(token)) """{"error":"cancelled"}"""
                else if (code in 200..299) text else """{"error":"HTTP $code"}"""
            } catch (e: Exception) {
                if (!safetyGate.responseAllowed(token)) """{"error":"cancelled"}"""
                else org.json.JSONObject().put("error", e.message ?: "network").toString()
            } finally {
                if (activeHttp === connection) activeHttp = null
                runCatching { connection?.disconnect() }
            }
        }

        @JavascriptInterface
        fun speak(text: String) {
            if (text.isBlank() || !safetyGate.effectsAllowed()) return
            // 留痕：没屏幕时靠 adb logcat 确认到底播了什么
            android.util.Log.i("BlindNavSay", text)
            runCatching {
                java.io.File(filesDir, "dialog.tsv")
                    .appendText(System.currentTimeMillis().toString() + "\tS\t" + text.replace("\t", " ") + "\n")
            }
            safetyGate.perform { TtsEngine.speak(text) }
        }

        @JavascriptInterface
        fun interruptSpeech() { if (safetyGate.effectsAllowed()) TtsEngine.interrupt() }

        @JavascriptInterface
        fun speakVisual(text: String, ageMs: Long, expectedVisualToken: Long) {
            if (text.isBlank() || ageMs !in 0L..3000L) return
            if (!pageVisible && CameraActivity.instance?.cameraActive != true) return
            if (expectedVisualToken != TtsEngine.routeVisualToken()) return
            val observedAt = System.nanoTime() - ageMs * 1_000_000L
            safetyGate.perform { TtsEngine.speakVisual(text, observedAtNanos = observedAt, expectedRouteToken = expectedVisualToken) }
        }

        @JavascriptInterface
        fun visualToken(): Long = TtsEngine.routeVisualToken()

        @JavascriptInterface
        fun acknowledgeSafety(token: Long) { safetyGate.acknowledge(token) }

        @JavascriptInterface
        fun setSpeed(s: Float) { TtsEngine.changeSpeed(s) }

        @JavascriptInterface
        fun setDirectionAssist(enabled: Boolean) {
            runOnUiThread { NavigationService.setDirectionAssist(enabled) }
        }

        @JavascriptInterface
        fun setMountMode(mode: String) {
            runOnUiThread { NavigationService.setMountMode(mode) }
        }

        /** 端侧盲道/斑马线检测：传 base64 图片，回 JSON。 */
        @JavascriptInterface
        fun pathLook(base64: String): String {
            val ticket = safetyGate.token()
            if (!safetyGate.responseAllowed(ticket)) return """{"error":"cancelled"}"""
            return try {
                val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
                val bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    ?: return """{"error":"图片解码失败"}"""
                val d = detector ?: BlindPathDetector(applicationContext).also { detector = it }
                val r = d.detect(bmp)
                if (!safetyGate.responseAllowed(ticket)) return """{"error":"cancelled"}"""
                PathObservation.from(r.onPath, r.offset, r.score, r.crosswalk, r.crosswalkScore, r.bend).json()
            } catch (e: Throwable) {
                if (e !is Exception && e !is LinkageError) throw e
                org.json.JSONObject().put("error", "盲道观察暂时不可用").toString()
            }
        }

        /** 端侧障碍检测：传 base64 图片，回 JSON。 */
        @JavascriptInterface
        fun obstacleLook(base64: String): String {
            return try {
                val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
                val bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    ?: return """{"error":"图片解码失败"}"""
                val d = obstacleDetector ?: ObstacleDetector(applicationContext)
                    .also { obstacleDetector = it }
                val hits = d.detect(bmp)
                val hit = hits.firstOrNull()
                val out = if (hit == null) """{"hit":false,"say":"前方走廊没有障碍"}"""
                else org.json.JSONObject()
                    .put("hit", true)
                    .put("count", hits.size)
                    .put("name", hit.name)
                    .put("side", hit.side)
                    .put("near", hit.near)
                    .put("band", hit.band)
                    .put("score", hit.score)
                    .put("say", "前方${hit.side}有${hit.name}" + if (hit.near) "，很近" else "")
                    .toString()
                android.util.Log.i("BlindNavDet", out)
                out
            } catch (e: Exception) {
                """{"error":"${e.message ?: "detect"}"}"""
            }
        }

        @JavascriptInterface
        fun startWatch() {
            val token=safetyGate.token()
            if (!safetyGate.responseAllowed(token)) return
            runOnUiThread {
                safetyGate.perform(token) {
                    if (CameraActivity.instance?.cameraActive != true)
                        startActivity(Intent(this@MainActivity, CameraActivity::class.java)
                            .putExtra("safetyToken", token)
                            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
                }
            }
        }

        @JavascriptInterface
        fun stopWatch() {
            runOnUiThread { CameraActivity.instance?.finish() }
        }

        @JavascriptInterface
        fun suppressPath(on: Boolean) { CameraActivity.setSuppressPath(on) }

        @JavascriptInterface
        fun isWatching(): Boolean = CameraActivity.instance?.cameraActive == true

        @JavascriptInterface
        fun cancelDoorSearch(): Long {
            TtsEngine.interrupt()
            return beginVisualCancellation()
        }

        /** 抓取连续看路里的当前画面（base64 JPEG），没有就返回空串。 */
        @JavascriptInterface
        fun grabFrame(): String = CameraActivity.instance?.grabJpeg() ?: ""

        @JavascriptInterface
        fun grabFramePacket(small: Boolean): String {
            val token=TtsEngine.routeVisualToken()
            val safety=safetyGate.token()
            if (!safetyGate.responseAllowed(safety)) return ""
            val raw=CameraActivity.instance?.grabJpegPacket(if(small) 512 else 768) ?: return ""
            if (!safetyGate.responseAllowed(safety) || token!=TtsEngine.routeVisualToken()) return ""
            return org.json.JSONObject(raw).put("visualToken",token).toString()
        }

        /** 小图：红绿灯这种只看一小块的，图小一点回得快 */
        @JavascriptInterface
        fun grabFrameSmall(): String = CameraActivity.instance?.grabJpeg(512) ?: ""

        /** 更小：只用来做"能不能过"的快检，省流量 */
        @JavascriptInterface
        fun grabFrameTiny(): String = CameraActivity.instance?.grabJpeg(384) ?: ""

        @JavascriptInterface
        fun pathState(): String = CameraActivity.instance?.pathState() ?: ""

        @JavascriptInterface
        fun groundState(): String = CameraActivity.instance?.groundState() ?: ""

        /** 提示音已停用：盲人的耳朵留给环境声，改用语音提醒。 */
        @JavascriptInterface
        fun beacon(pan: Float) { }

        @JavascriptInterface
        fun stopBeacon() { }

        /** 把用户说的每句话存到本地，攒够真实语料再训端侧模型。 */
        @JavascriptInterface
        fun logUtterance(text: String) {
            try {
                val f = java.io.File(filesDir, "utterances.tsv")
                f.appendText(System.currentTimeMillis().toString() + "\t" + text.replace("\t", " ") + "\n")
            } catch (e: Exception) { }
        }

        /** 把路线交给后台服务，锁屏也能继续播报。 */
        @JavascriptInterface
        fun startNav(dest: String, lng: Double, lat: Double, steps: String) {
            safetyGate.perform {
                TtsEngine.invalidateRouteVisual()
                val i = Intent(this@MainActivity, NavigationService::class.java)
                    .putExtra("dest", dest)
                    .putExtra("lng", lng)
                    .putExtra("lat", lat)
                    .putExtra("steps", steps)
                    .putExtra("safetyToken", NativeSafety.navigation.token())
                androidx.core.content.ContextCompat.startForegroundService(this@MainActivity, i)
            }
        }

        @JavascriptInterface
        fun stopNav(): Long {
            val token = safetyGate.beginSafetyCommand()
            NavigationService.cancel()
            stopService(Intent(this@MainActivity, NavigationService::class.java))
            TtsEngine.interrupt()
            return token
        }

        @JavascriptInterface
        fun navState(): String = NavigationService.navigationState

        @JavascriptInterface
        fun navSession(): String = NavigationService.liveSessionSnapshot()

        @JavascriptInterface
        fun mapCoordinates(lng: Double, lat: Double): String = try {
            val p = MapCoordinates.fromGps(this@MainActivity, lng, lat)
            org.json.JSONObject().put("lng", p.first).put("lat", p.second).toString()
        } catch (e: Exception) { "{}" }

        /** 手机粗磁方位；平放参考顶部，竖持参考后置镜头；不代表身体前进方向。 */
        @JavascriptInterface
        fun heading(): Float = phoneDirection.heading(android.os.SystemClock.elapsedRealtimeNanos())?.degrees ?: -1f

        @JavascriptInterface
        fun cameraHeading(): Float = phoneDirection.cameraHeading(android.os.SystemClock.elapsedRealtimeNanos())?.degrees ?: -1f

        @JavascriptInterface
        fun cameraUpright(): Boolean = phoneDirection.cameraUpright(android.os.SystemClock.elapsedRealtimeNanos())

        @JavascriptInterface
        fun directionState(): String = phoneDirection.snapshot(android.os.SystemClock.elapsedRealtimeNanos())

        /** 手机相对参考角，不表示北向或身体方向；不可用时返回-1。 */
        @JavascriptInterface
        fun gyroHeading(): Float = phoneDirection.gyroHeading(android.os.SystemClock.elapsedRealtimeNanos())?.degrees ?: -1f

        /** 语音识别：端侧模型，识别完就停。 */
        @JavascriptInterface
        fun listen() {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                TtsEngine.speak("还没有麦克风权限，请点允许")
                runOnUiThread { askNextPermission() }
                return
            }
            // 按钮触发：只听说一句，说完就关，避免把周围聊天都收进来
            dialogOn = false
            val ticket = listeningGate.once()
            val requestUntil = System.nanoTime() + 15_000_000_000L
            singleListenUntilNanos = requestUntil
            runOnUiThread {
                if (singleListenUntilNanos != requestUntil || System.nanoTime() >= requestUntil) return@runOnUiThread
                listeningGate.perform(ticket) {
                    engine().start()
                    TtsEngine.ifIdle { if (singleListenUntilNanos == requestUntil) asrEngine?.resume() }
                }
            }
            // 15 秒内没人说话就关麦，别一直开着
            web.postDelayed({
                if (!dialogOn && singleListenUntilNanos == requestUntil) {
                    singleListenUntilNanos = 0L
                    runCatching { asrEngine?.stop() }
                }
            }, 15000)
        }

        /** 连续对话：一直听，直到 stopDialog。 */
        @JavascriptInterface
        fun startDialog() { startDialog(false) }

        @JavascriptInterface
        fun startAutoDialog(): Boolean = startDialog(true)

        private fun startDialog(auto: Boolean): Boolean {
            android.util.Log.i("BlindNavASR", "startDialog: permsOk=" +
                perms.none { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED })
            if (perms.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) {
                TtsEngine.speak("请先把屏幕上的权限按钮点允许，我才能听你说话")
                runOnUiThread { askNextPermission() }
                return false
            }
            val ticket = if (auto) listeningGate.automatic() ?: return false else listeningGate.manual()
            runOnUiThread {
                listeningGate.perform(ticket) {
                    dialogOn = true
                    singleListenUntilNanos = 0L
                    // 前台服务保住麦克风：锁屏/后台也能听
                    runCatching {
                        if (android.os.Build.VERSION.SDK_INT >= 26)
                            startForegroundService(Intent(this@MainActivity, ListenService::class.java))
                        else startService(Intent(this@MainActivity, ListenService::class.java))
                    }
                    engine().start()
                }
            }
            return true
        }

        @JavascriptInterface
        fun stopDialog() {
            val ticket = listeningGate.stop()
            dialogOn = false
            singleListenUntilNanos = 0L
            runOnUiThread {
                listeningGate.perform(ticket) {
                    runCatching { asrEngine?.stop() }
                    runCatching { stopService(Intent(this@MainActivity, ListenService::class.java)) }
                }
            }
        }
    }
}

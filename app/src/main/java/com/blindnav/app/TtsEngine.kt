package com.blindnav.app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsMatchaModelConfig

/**
 * 离线中文语音合成（sherpa-onnx + VITS），带播报队列，避免后一句把前一句切断。
 * 国产 ROM 的系统 TTS 常不给第三方 App 用，所以自带。
 */
object TtsEngine {

    private const val TAG = "BlindNavTTS"
    private var tts: OfflineTts? = null
    private var track: AudioTrack? = null
    private val queue = ArrayDeque<Speech>()
    private val lock = Object()
    private var worker: Thread? = null

    @Volatile
    var speaking = false
        private set

    /** 语速倍数，1.0 正常；盲人常要求放慢 */
    @Volatile
    var speed = 1.0f

    fun changeSpeed(s: Float) { speed = s.coerceIn(0.5f, 1.5f) }

    var onStart: (() -> Unit)? = null
    var onDone: (() -> Unit)? = null

    @Volatile
    private var initStarted = false

    fun init(context: Context) {
        synchronized(this) {
            if (initStarted) return
            initStarted = true
        }
        Thread { doInit(context) }.start()
    }

    private fun doInit(context: Context) {
        try {
            val config = OfflineTtsConfig(
                model = OfflineTtsModelConfig(
                    matcha = OfflineTtsMatchaModelConfig(
                        acousticModel = "tts/model.onnx",
                        vocoder = "tts/vocoder.onnx",
                        lexicon = "tts/lexicon.txt",
                        tokens = "tts/tokens.txt",
                        dictDir = "tts/dict",
                        noiseScale = 0.667f,
                        lengthScale = 1.0f,
                    ),
                    numThreads = 2,
                    debug = false,
                ),
                ruleFsts = "tts/date.fst,tts/number.fst,tts/phone.fst",
            )
            tts = OfflineTts(context.assets, config)
            Log.i(TAG, "offline tts ready, speakers=${tts?.numSpeakers()} sr=${tts?.sampleRate()}")
            worker = Thread { loop() }.also { it.start() }
        } catch (e: Throwable) {
            Log.e(TAG, "offline tts init failed", e)
            initStarted = false
        }
    }

    @Volatile private var generation = 0L
    private data class Speech(val text: String, val observedAt: Long? = null, val visualToken: Long = 0L, val routeToken: Long? = null,
                              val direction: Boolean = false, val hazard: Boolean = false, val cameraGuidance: Boolean = false)
    @Volatile private var visualGeneration = 0L
    @Volatile private var routeVisualGeneration = 0L
    private var playingVisual = false
    private var playingRouteVisual = false
    private var playingDirection = false
    private var playingHazard = false
    private var playingCameraGuidance = false
    private val speechDeduper = SpeechDeduper()
    @Volatile private var cameraGuidanceAllowed = true
    private var cameraGuidanceOwner: Any? = null

    private fun isCameraGuidance(job: Speech): Boolean = !job.hazard && (job.direction || job.cameraGuidance)

    /** The active camera owns this gate; an older activity cannot reopen a newer session. */
    fun beginCameraGuidance(owner: Any) {
        synchronized(lock) {
            cameraGuidanceOwner = owner
            cameraGuidanceAllowed = false
            cancelCameraGuidanceLocked()
        }
    }

    fun setCameraGuidanceAllowed(owner: Any, allowed: Boolean) {
        synchronized(lock) {
            if (cameraGuidanceOwner !== owner) return
            cameraGuidanceAllowed = allowed
            if (!allowed) cancelCameraGuidanceLocked()
        }
    }

    fun endCameraGuidance(owner: Any) {
        synchronized(lock) {
            if (cameraGuidanceOwner !== owner) return
            cameraGuidanceOwner = null
            cameraGuidanceAllowed = true
        }
    }

    private fun cancelCameraGuidanceLocked() {
        queue.removeAll { isCameraGuidance(it) }
        if (playingCameraGuidance) cancelLocked()
        lock.notifyAll()
    }

    private fun current(job: Speech): Boolean {
        if (isCameraGuidance(job) && !cameraGuidanceAllowed) return false
        return job.observedAt?.let {
            job.visualToken == visualGeneration && (job.routeToken == null || job.routeToken == routeVisualGeneration) && System.nanoTime() - it in 0L..3_000_000_000L
        } ?: true
    }

    fun visualToken(): Long = visualGeneration
    fun routeVisualToken(): Long = routeVisualGeneration

    /** 延迟恢复麦克风前原子检查；新消息合成/播放期间不能恢复。 */
    fun ifIdle(action: () -> Unit) {
        synchronized(lock) { if (queue.isEmpty() && !speaking) action() }
    }

    /** 目标变化取消手动/入口观察，不打断独立相机危险提示。 */
    fun invalidateRouteVisual() {
        synchronized(lock) {
            routeVisualGeneration++
            queue.removeAll { it.routeToken != null }
            if (playingRouteVisual) cancelLocked()
            lock.notifyAll()
        }
    }

    /** 画面消息在播放开始前检查时效；开始后允许说完整，失效/取消仍可打断。 */
    fun speakVisual(text: String, urgent: Boolean = false, observedAtNanos: Long = System.nanoTime(),
                    expectedVisualToken: Long = visualGeneration, expectedRouteToken: Long? = null, hazard: Boolean = false, cameraGuidance: Boolean = false) {
        enqueue(text, urgent, observedAtNanos, expectedVisualToken, expectedRouteToken, hazard = hazard, cameraGuidance = cameraGuidance)
    }

    fun speakDirection(text: String, observedAtNanos: Long, urgent: Boolean = false) {
        synchronized(lock) {
            if (text.isBlank() || System.nanoTime()-observedAtNanos !in 0L..3_000_000_000L) return
            if (playingHazard || queue.any { it.hazard && current(it) }) return
            queue.removeAll { it.direction }
            enqueue(text,urgent,observedAtNanos,expectedRouteToken=routeVisualGeneration,direction=true)
        }
    }

    fun invalidateDirection() {
        synchronized(lock) {
            queue.removeAll { it.direction }
            if (playingDirection) cancelLocked()
            lock.notifyAll()
        }
    }

    /** 相机或页面失效只取消画面消息，保留路线/对话队列。 */
    fun invalidateVisual() {
        synchronized(lock) {
            visualGeneration++
            routeVisualGeneration++
            queue.removeAll { it.observedAt != null }
            if (playingVisual) cancelLocked()
            lock.notifyAll()
        }
    }

    fun speak(text: String, urgent: Boolean = false) {
        enqueue(text, urgent, null)
    }

    /**
     * 只读查询：现在有没有"危险/STOP"级播报正在播或还在排队。
     * 最后十米的 P1 提示用它在开口前让路（PF-02：最后十米永远不许打断 STOP）。
     * 这里不改任何播报行为，只是把已有状态暴露出来。
     */
    fun stopLevelActive(): Boolean = synchronized(lock) {
        playingHazard || queue.any { it.hazard }
    }

    private fun enqueue(text: String, urgent: Boolean, observedAt: Long?, expectedVisualToken: Long = visualGeneration,
                        expectedRouteToken: Long? = null, direction: Boolean = false, hazard: Boolean = false, cameraGuidance: Boolean = false) {
        if (text.isBlank()) return
        Log.i(TAG, "say: $text")   // 没屏幕时靠 logcat 核对到底播了什么
        synchronized(lock) {
            if (observedAt == null && !direction && !hazard && !cameraGuidance &&
                !speechDeduper.accept(text, System.nanoTime() / 1_000_000L, urgent)) return
            val job = Speech(text, observedAt, expectedVisualToken, expectedRouteToken, direction, hazard, cameraGuidance)
            if (!current(job)) return
            if (hazard) {
                queue.removeAll { it.direction }
                if (playingDirection && !urgent) cancelLocked()
            }
            if (urgent) {
                queue.clear()
                cancelLocked()
            }
            queue.addLast(job)
            lock.notifyAll()
        }
    }

    /** 打断：清空队列并停掉当前播放（用户开口时用）。 */
    fun interrupt() {
        synchronized(lock) {
            queue.clear()
            speechDeduper.reset()
            cancelLocked()
        }
    }

    fun stop() {
        synchronized(lock) { cancelLocked() }
    }

    /** 同时取消正在合成和正在播放的旧消息，唤醒播放等待。调用者持有 lock。 */
    private fun cancelLocked() {
        generation++
        playingVisual = false
        playingRouteVisual = false
        playingDirection = false; playingHazard = false; playingCameraGuidance = false
        val previous = track
        track = null
        runCatching { previous?.pause(); previous?.flush(); previous?.release() }
        lock.notifyAll()
    }

    private fun loop() {
        while (true) {
            val job = synchronized(lock) {
                while (queue.isEmpty()) lock.wait()
                val next=queue.removeFirst()
                speaking=true
                playingVisual=next.observedAt!=null; playingRouteVisual=next.routeToken!=null
                playingDirection=next.direction; playingHazard=next.hazard; playingCameraGuidance=isCameraGuidance(next)
                next to generation
            }
            play(job.first, job.second)
        }
    }

    @Volatile
    private var lastSpokeAt = 0L

    private fun play(job: Speech, token: Long) {
        var currentTrack: AudioTrack? = null
        try {
            val engine = tts ?: return
            if (token != generation || !current(job)) return
            val text = job.text
            lastSpokeAt = System.currentTimeMillis()
            speaking = true
            val audio = engine.generateWithCallback(text, 0, speed,
                TtsCallback { token == generation && current(job) })
            if (token != generation || !current(job)) return
            val samples = audio.samples
            val sr = audio.sampleRate
            val pcm = ShortArray(samples.size) { (samples[it] * 32767).toInt().toShort() }
            val at = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sr)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setBufferSizeInBytes(pcm.size * 2)
                .setTransferMode(AudioTrack.MODE_STATIC)
                .build()
            currentTrack = at
            at.write(pcm, 0, pcm.size)
            synchronized(lock) {
                if (token != generation || !current(job)) return
                track = at
                playingVisual = job.observedAt != null
                playingRouteVisual = job.routeToken != null
                playingDirection = job.direction; playingHazard = job.hazard; playingCameraGuidance = isCameraGuidance(job)
                onStart?.invoke()
                at.play()
                val end = System.nanoTime() + ((pcm.size * 1000L / sr) + 60) * 1_000_000L
                while (token == generation && (job.observedAt == null || job.visualToken == visualGeneration) &&
                    (job.routeToken == null || job.routeToken == routeVisualGeneration)) {
                    val remaining = end - System.nanoTime()
                    if (remaining <= 0) break
                    lock.wait(maxOf(1L, remaining / 1_000_000L))
                }
            }
        } catch (e: Throwable) {
            Log.e(TAG, "play failed", e)
        } finally {
            val more = synchronized(lock) {
                runCatching { currentTrack?.release() }
                if (track === currentTrack) {
                    track = null; playingVisual = false; playingRouteVisual = false
                    playingDirection = false; playingHazard = false; playingCameraGuidance = false
                }
                queue.isNotEmpty()
            }
            if (!more) {
                speaking = false
                onDone?.invoke()
            }
        }
    }
}

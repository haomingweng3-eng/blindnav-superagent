package com.blindnav.app

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineSenseVoiceModelConfig

/**
 * 中文语音识别：Silero VAD 切出一句话，再用 SenseVoice 整句识别。
 * 比小尺寸流式模型准得多，代价是这句话说完才有结果（约 0.3-0.6 秒）。
 */
class AsrEngine(
    private val context: Context,
    private val onFinal: (String) -> Unit,
    private val onPartial: (String) -> Unit = {},
    private val onInterrupt: () -> Unit = {},
    private val onMicSilent: () -> Unit = {},
    private val onNoSpeech: () -> Unit = {},
) {
    private var recognizer: OfflineRecognizer? = null
    private var record: AudioRecord? = null
    private val vad: Vad? by lazy { runCatching { Vad(context) }.getOrNull() }

    @Volatile private var running = false
    @Volatile private var paused = false
    private var thread: Thread? = null
    private val sampleRate = 16000

    private fun ensureRecognizer(): Boolean {
        if (recognizer != null) return true
        return try {
            val cfg = OfflineRecognizerConfig(
                featConfig = FeatureConfig(sampleRate = sampleRate, featureDim = 80),
                modelConfig = OfflineModelConfig(
                    senseVoice = OfflineSenseVoiceModelConfig(
                        model = "asr/sense/model.int8.onnx",
                        language = "zh",
                        useInverseTextNormalization = true,
                    ),
                    tokens = "asr/sense/tokens.txt",
                    numThreads = 2,
                    debug = false,
                ),
            )
            recognizer = OfflineRecognizer(context.assets, cfg)
            Log.i(TAG, "sensevoice ready")
            true
        } catch (e: Throwable) {
            Log.e(TAG, "init failed", e)
            false
        }
    }

    fun start() {
        Log.i(TAG, "start() running=$running")
        if (running) return
        Thread {
            if (!ensureRecognizer()) return@Thread
            running = true
            loop()
        }.also { thread = it; it.start() }
    }

    fun pause() { paused = true }
    fun resume() { paused = false }

    fun stop() {
        running = false
        runCatching { record?.stop() }
        runCatching { thread?.join(600) }
        runCatching { record?.release() }
        record = null
    }

    private fun loop() {
        val minBuf = AudioRecord.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, sampleRate / 2)
            )
        } catch (e: Throwable) {
            Log.e(TAG, "AudioRecord failed", e); return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord not initialized"); rec.release(); return
        }
        record = rec
        rec.startRecording()

        val buf = ShortArray(sampleRate / 10)   // 100ms
        val pre = ArrayDeque<FloatArray>()      // 说话前的少量缓存，别把第一个字切掉
        val utter = ArrayList<FloatArray>()
        var speaking = false
        var lastVoice = 0L
        var utterSamples = 0
        var zeroRun = 0
        var lastLevelLog = 0L
        var loud = 0
        val startedAt = System.currentTimeMillis()

        val interruptPrefix = ArrayDeque<FloatArray>()
        var wasPaused = false
        fun resetUtterance() {
            speaking = false; utter.clear(); pre.clear(); utterSamples = 0
            vad?.reset()
        }
        fun normalizedSamples(input: FloatArray, level: Double): FloatArray {
            val samples = input
            if (level > 1e-4 && level < 0.05) {
                val g = (0.12 / level).coerceAtMost(30.0).toFloat()
                for (i in samples.indices) samples[i] = (samples[i] * g).coerceIn(-1f, 1f)
            }

            return samples
        }
        fun consume(input: FloatArray, level: Double, now: Long) {
            val samples = normalizedSamples(input, level)
            val p = voiceProb(samples)
            if (p > 0.45f) { speaking = true; lastVoice = now }
            else if (speaking && p < 0.30f && now - lastVoice > 600) {
                // 一句话说完了
                finalizeUtterance(pre, utter)
                speaking = false; utter.clear(); pre.clear(); utterSamples = 0
                vad?.reset()
            }

            if (!speaking) {
                pre.addLast(samples)
                if (pre.size > 3) pre.removeFirst()
            } else {
                utter.add(samples)
                utterSamples += samples.size
                if (utterSamples > sampleRate * 12) {   // 最长 12 秒，防卡死
                    finalizeUtterance(pre, utter)
                    utter.clear(); pre.clear(); utterSamples = 0; speaking = false
                }
            }
        }

        while (running) {
            val n = rec.read(buf, 0, buf.size)
            if (n <= 0) continue

            var sq = 0.0
            var allZero = true
            for (i in 0 until n) {
                if (buf[i].toInt() != 0) allZero = false
                val v = buf[i] / 32768.0; sq += v * v
            }
            val level = Math.sqrt(sq / n)
            val now = System.currentTimeMillis()

            if (paused) {
                if (!wasPaused) {
                    resetUtterance()
                    interruptPrefix.clear()
                    loud = 0
                    wasPaused = true
                }
                // Only retain the two consecutive trigger blocks, never a TTS history buffer.
                if (level > 0.02) {
                    interruptPrefix.addLast(FloatArray(n) { buf[it] / 32768.0f })
                    if (interruptPrefix.size > 2) interruptPrefix.removeFirst()
                    if (++loud >= 2) {
                        loud = 0
                        val detector = vad
                        val triggerVoice = if (detector == null || !detector.isHealthy()) true else try {
                            detector.reset()
                            interruptPrefix.any { chunk ->
                                voiceProb(normalizedSamples(chunk.copyOf(), energy(chunk))) > 0.45f
                            } || !detector.isHealthy()
                        } finally {
                            detector.reset()
                        }
                        if (!triggerVoice) {
                            interruptPrefix.clear()
                            continue
                        }
                        onInterrupt()
                        // MainActivity synchronously interrupts TTS and resumes listening.
                        // If still paused, none of these blocks may enter recognition.
                        if (!paused) {
                            resetUtterance()
                            for (chunk in interruptPrefix) {
                                if (paused) break
                                consume(chunk, energy(chunk), now)
                            }
                            if (paused) resetUtterance()
                            wasPaused = paused
                        }
                        interruptPrefix.clear()
                    }
                } else {
                    loud = 0
                    interruptPrefix.clear()
                }
                continue
            }
            loud = 0

            if (now - startedAt > 2000) {
                if (allZero) zeroRun++ else zeroRun = 0
            }
            if (now - lastLevelLog > 5000) {
                lastLevelLog = now
                Log.d(TAG, "level=%.3f speaking=%s".format(level, speaking))
            }
            if (zeroRun > 50) {
                // 连续 5 秒纯零：麦克风被系统或别的应用占了
                runCatching { onMicSilent() }
                zeroRun = 0
            }
            if (allZero && !speaking) continue

            wasPaused = false
            interruptPrefix.clear()
            consume(FloatArray(n) { buf[it] / 32768.0f }, level, now)
        }
        rec.stop(); rec.release(); record = null
    }

    /** 取这段音频里 VAD 的最大人声概率。 */
    private fun voiceProb(samples: FloatArray): Float {
        val v = vad ?: return if (energy(samples) > 0.01) 1f else 0f
        var max = 0f
        var i = 0
        while (i + 512 <= samples.size) {
            max = maxOf(max, v.prob(samples.copyOfRange(i, i + 512)))
            i += 512
        }
        return max
    }

    private fun energy(s: FloatArray): Double {
        var sum = 0.0
        for (v in s) sum += v * v
        return Math.sqrt(sum / s.size)
    }

    private fun finalizeUtterance(pre: ArrayDeque<FloatArray>, utter: List<FloatArray>) {
        val chunks = ArrayList<FloatArray>()
        chunks.addAll(pre)
        chunks.addAll(utter)
        if (chunks.isEmpty()) return
        val total = chunks.sumOf { it.size }
        if (total < sampleRate / 4) return   // 太短，多半是咳嗽/噪声
        val audio = FloatArray(total)
        var off = 0
        for (c in chunks) { System.arraycopy(c, 0, audio, off, c.size); off += c.size }
        val text = recognize(audio).trim()
        if (text.isEmpty()) {
            Log.i(TAG, "no text (%.1fs audio)".format(total / sampleRate.toFloat()))
            runCatching { onNoSpeech() }
        } else if (containsLetterOrDigit(text)) {
            Log.i(TAG, "final: $text")
            onFinal(text)
        }
    }

    /** 噪声可能只解出标点或符号；保留包括单字在内的语言内容。 */
    private fun containsLetterOrDigit(text: String): Boolean {
        var offset = 0
        while (offset < text.length) {
            val codePoint = text.codePointAt(offset)
            if (Character.isLetterOrDigit(codePoint)) return true
            offset += Character.charCount(codePoint)
        }
        return false
    }

    private fun recognize(audio: FloatArray): String {
        val r = recognizer ?: return ""
        return try {
            val stream = r.createStream()
            stream.acceptWaveform(audio, sampleRate)
            r.decode(stream)
            val text = r.getResult(stream).text
            stream.release()
            text
        } catch (e: Throwable) {
            Log.e(TAG, "decode failed", e); ""
        }
    }

    /** 自测：把内置语音直接送进识别器。 */
    fun selfTest() {
        Thread {
            if (!ensureRecognizer()) return@Thread
            try {
                val audio = readWav("asr/selftest.wav")
                val text = recognize(audio).trim()
                Log.i(TAG, "selftest final: $text")
                if (containsLetterOrDigit(text)) onFinal(text)
            } catch (e: Throwable) { Log.e(TAG, "selftest failed", e) }
        }.start()
    }

    /** 诊断：录 4 秒麦克风到 files/mic.wav。 */
    fun dumpMic(seconds: Int = 4) {
        Thread {
            try {
                val minBuf = AudioRecord.getMinBufferSize(
                    sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, sampleRate))
                rec.startRecording()
                val total = sampleRate * seconds
                val buf = ShortArray(total)
                var got = 0
                while (got < total) {
                    val n = rec.read(buf, got, minOf(4096, total - got))
                    if (n <= 0) break
                    got += n
                }
                rec.stop(); rec.release()
                val f = java.io.File(context.filesDir, "mic.wav")
                val dataLen = got * 2
                val hdr = java.nio.ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                hdr.put("RIFF".toByteArray()); hdr.putInt(36 + dataLen); hdr.put("WAVEfmt ".toByteArray())
                hdr.putInt(16); hdr.putShort(1); hdr.putShort(1)
                hdr.putInt(sampleRate); hdr.putInt(sampleRate * 2)
                hdr.putShort(2); hdr.putShort(16); hdr.put("data".toByteArray()); hdr.putInt(dataLen)
                val pcm = java.nio.ByteBuffer.allocate(dataLen).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                for (i in 0 until got) pcm.putShort(buf[i])
                f.outputStream().use { it.write(hdr.array()); it.write(pcm.array()) }
                Log.i(TAG, "mic.wav saved, samples=$got")
            } catch (e: Throwable) { Log.e(TAG, "dumpMic failed", e) }
        }.start()
    }

    private fun readWav(asset: String): FloatArray {
        val bytes = context.assets.open(asset).readBytes()
        var off = 12
        while (off + 8 < bytes.size) {
            val id = String(bytes, off, 4, Charsets.US_ASCII)
            val size = (bytes[off + 4].toInt() and 0xff) or
                    ((bytes[off + 5].toInt() and 0xff) shl 8) or
                    ((bytes[off + 6].toInt() and 0xff) shl 16) or
                    ((bytes[off + 7].toInt() and 0xff) shl 24)
            if (id == "data") { off += 8; break }
            off += 8 + size + (size and 1)
        }
        val len = (bytes.size - off) / 2
        return FloatArray(len) {
            val lo = bytes[off + it * 2].toInt() and 0xff
            val hi = bytes[off + it * 2 + 1].toInt()
            ((hi shl 8) or lo).toShort() / 32768f
        }
    }

    fun release() {
        stop()
        runCatching { recognizer?.release() }
        recognizer = null
    }

    companion object { private const val TAG = "BlindNavASR" }
}

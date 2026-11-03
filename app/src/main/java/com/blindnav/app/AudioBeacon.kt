package com.blindnav.app

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/**
 * 方向提示音：把一声短促的"嘟"按方位放到左/右耳，靠声音方位引导，
 * 不依赖"左转/右转"这类词（Wayfindr / Soundscape 的做法）。
 */
object AudioBeacon {

    @Volatile
    private var track: AudioTrack? = null

    /** pan: -1 全左，0 正中，+1 全右 */
    fun beep(pan: Float) {
        try {
            val sr = 44100
            val ms = 140
            val n = sr * ms / 1000
            val pcm = ShortArray(n * 2)   // 立体声
            val freq = 720.0
            val p = pan.coerceIn(-1f, 1f)
            // 等功率声像
            val theta = ((p + 1f) * 0.25f * Math.PI).toFloat()
            val gl = Math.cos(theta.toDouble()).toFloat()
            val gr = Math.sin(theta.toDouble()).toFloat()
            for (i in 0 until n) {
                val t = i.toDouble() / sr
                // 淡入淡出，避免爆音
                val env = when {
                    i < n * 0.1 -> i / (n * 0.1)
                    i > n * 0.8 -> (n - i) / (n * 0.2)
                    else -> 1.0
                }
                val s = (Math.sin(2 * Math.PI * freq * t) * 0.35 * env * 32767).toInt()
                pcm[i * 2] = (s * gl).toInt().toShort()
                pcm[i * 2 + 1] = (s * gr).toInt().toShort()
            }
            val at = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sr)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                        .build()
                )
                .setBufferSizeInBytes(pcm.size * 2)
                .setTransferMode(AudioTrack.MODE_STATIC)
                .build()
            at.write(pcm, 0, pcm.size)
            at.play()
            at.setNotificationMarkerPosition(n)
            at.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(t: AudioTrack?) { runCatching { t?.release() } }
                override fun onPeriodicNotification(t: AudioTrack?) {}
            })
            track?.let { runCatching { it.release() } }
            track = at
        } catch (e: Throwable) {
            // 提示音失败不影响主流程
        }
    }

    /** 提示音：attention=要说重要内容(两声上行)，listen=开始收音(轻一声)，warn=警告(低音) */
    fun cue(kind: String) {
        when (kind) {
            "attention" -> tone(doubleArrayOf(880.0, 1180.0), 90)
            "listen" -> tone(doubleArrayOf(1250.0), 55)
            "warn" -> tone(doubleArrayOf(420.0, 380.0), 110)
            else -> tone(doubleArrayOf(800.0), 70)
        }
    }

    private fun tone(freqs: DoubleArray, msPerTone: Int) {
        try {
            val sr = 44100
            val per = sr * msPerTone / 1000
            val total = per * freqs.size + (freqs.size - 1) * (sr * 40 / 1000)
            val pcm = ShortArray(total)
            var off = 0
            for (f in freqs) {
                for (i in 0 until per) {
                    val env = when {
                        i < per * 0.15 -> i / (per * 0.15)
                        i > per * 0.75 -> (per - i) / (per * 0.25)
                        else -> 1.0
                    }
                    val s = (Math.sin(2 * Math.PI * f * i / sr) * 0.25 * env * 32767).toInt()
                    if (off + i < total) pcm[off + i] = s.toShort()
                }
                off += per + (sr * 40 / 1000)
            }
            val at = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                .setAudioFormat(AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(sr)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setBufferSizeInBytes(pcm.size * 2)
                .setTransferMode(AudioTrack.MODE_STATIC).build()
            at.write(pcm, 0, pcm.size)
            at.play()
            Thread { try { Thread.sleep(((pcm.size * 1000L) / sr) + 120) } catch (e: Exception) {} finally { runCatching { at.release() } } }.start()
        } catch (e: Throwable) { }
    }

    fun stop() {
        runCatching { track?.pause(); track?.release() }
        track = null
    }
}

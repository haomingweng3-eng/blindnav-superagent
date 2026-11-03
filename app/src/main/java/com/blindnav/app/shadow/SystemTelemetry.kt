package com.blindnav.app.shadow

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.os.Process

/**
 * 手机侧资源遥测（任务书第十八/十九节）。
 *
 * 全部"读得到就记，读不到就 null"，不轮询高频：热状态与内存按秒级采样即可，
 * 因为这一节要回答的是"连续 10–20 分钟后有没有降频"，不是每一帧的占用。
 */
class SystemTelemetry(private val context: Context) {

    @Volatile var thermalStatus: Int = -1
        private set

    fun snapshot(): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
        if (pm != null && Build.VERSION.SDK_INT >= 29) {
            thermalStatus = runCatching { pm.currentThermalStatus }.getOrDefault(-1)
            out["thermal_status"] = thermalStatus
            // getThermalHeadroom(int) 是 API 34 才有的，读不到就留空，不影响其它遥测。
            if (Build.VERSION.SDK_INT >= 34) {
                runCatching { out["thermal_headroom"] = pm.getThermalHeadroom(0) }
            }
        }
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        if (am != null) {
            val mem = ActivityManager.MemoryInfo()
            runCatching { am.getProcessMemoryInfo(intArrayOf(Process.myPid())) }
                .getOrNull()?.firstOrNull()?.let { info ->
                    out["pss_kb"] = info.totalPss.toLong()
                    out["private_dirty_kb"] = info.totalPrivateDirty.toLong()
                }
            runCatching { am.getMemoryInfo(mem) }
            out["app_memory_class_mb"] = runCatching { am.memoryClass.toDouble() }.getOrNull()
        }
        val runtime = Runtime.getRuntime()
        out["java_heap_used_mb"] = (runtime.totalMemory() - runtime.freeMemory()) / 1048576.0
        out["java_heap_max_mb"] = runtime.maxMemory() / 1048576.0
        val debugMemory = Debug.getNativeHeapAllocatedSize() / 1048576.0
        out["native_heap_mb"] = debugMemory
        out["battery_pct"] = batteryPercent()
        out["cpu_cores"] = Runtime.getRuntime().availableProcessors()
        out["thread_count"] = runCatching {
            java.io.File("/proc/self/task").listFiles()?.size ?: 0
        }.getOrDefault(0)
        return out
    }

    private fun batteryPercent(): Int? = try {
        val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        if (level >= 0 && scale > 0) level * 100 / scale else null
    } catch (e: Throwable) {
        null
    }

    /** 进程 CPU 占用（/proc/self/stat 的 utime+stime 差值）。取不到返回 null。 */
    class CpuSampler {
        private var lastTicks: Long? = null
        private var lastAt: Long? = null

        fun sample(): Double? {
            val line = runCatching { java.io.File("/proc/self/stat").readText().trim() }
                .getOrNull()?.lines()?.firstOrNull() ?: return null
            val fields = line.substringAfterLast(')').trim().split(' ')
            // utime=13, stime=14（去掉 pid/comm/state 之后）
            val utime = fields.getOrNull(11)?.toLongOrNull() ?: return null
            val stime = fields.getOrNull(12)?.toLongOrNull() ?: return null
            val ticks = utime + stime
            val now = android.os.SystemClock.elapsedRealtime()
            val previousTicks = lastTicks
            val previousNow = lastAt
            lastTicks = ticks
            lastAt = now
            if (previousTicks == null || previousNow == null || now <= previousNow) return null
            val hz = 100L   // Android 内核 USER_HZ 固定 100
            return (ticks - previousTicks).toDouble() / hz / ((now - previousNow) / 1000.0) * 100.0
        }
    }
}

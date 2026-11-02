package com.blindnav.app.shadow

import android.content.Context
import com.blindnav.app.CameraActivity

/**
 * Shadow Mode 开关。全部默认关闭：生产导航链路（CameraX + CameraActivity + TtsEngine 播报）
 * 不会因为装上这个包而改变行为，只有从 ArShadowActivity 显式打开才生效。
 *
 * 设计约束（来自 Android 真机阶段任务书）：
 *  - 新能力一律先影子模式，只写日志和 Debug Overlay，不产生方向播报；
 *  - ARCore 与 CameraX 不得同时拥有后摄像头，见 [ArCameraOwnership]。
 */
class ShadowFlags(context: Context) {

    private val sp = context.applicationContext.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    /** ARCore Session 作为相机/空间主时钟（影子链路总开关）。 */
    var arcoreOwner: Boolean
        get() = sp.getBoolean(KEY_ARCORE, false)
        set(v) = sp.edit().putBoolean(KEY_ARCORE, v).apply()

    /** BlindNavSensorLogger 落盘 JSONL。关闭时只保留内存里的最近若干条。 */
    var sensorLog: Boolean
        get() = sp.getBoolean(KEY_LOG, false)
        set(v) = sp.edit().putBoolean(KEY_LOG, v).apply()

    /** Dense segmentation / Scene Semantics 实验（任务书 ENABLE_TRAVERSABILITY_EXPERIMENT）。 */
    var traversabilityExperiment: Boolean
        get() = sp.getBoolean(KEY_TRAVERSABILITY, false)
        set(v) = sp.edit().putBoolean(KEY_TRAVERSABILITY, v).apply()

    /** WorldPath 影子：算出 lateral/heading error 与 would_have_said，但不播报。 */
    var worldPathShadow: Boolean
        get() = sp.getBoolean(KEY_WORLDPATH, false)
        set(v) = sp.edit().putBoolean(KEY_WORLDPATH, v).apply()

    /** Safe Motion Authority 影子：只记录 would_have_said / would_stop。 */
    var safeMotionShadow: Boolean
        get() = sp.getBoolean(KEY_SAFE_MOTION, false)
        set(v) = sp.edit().putBoolean(KEY_SAFE_MOTION, v).apply()

    /** 最后十米关系数据采集。 */
    var last10mCapture: Boolean
        get() = sp.getBoolean(KEY_LAST10M, false)
        set(v) = sp.edit().putBoolean(KEY_LAST10M, v).apply()

    /** 每 N 帧落一条 FRAME 事件；1 = 每帧。性能不足时调大，而不是排队。 */
    var frameSampleEvery: Int
        get() = sp.getInt(KEY_FRAME_SAMPLE, 1).coerceAtLeast(1)
        set(v) = sp.edit().putInt(KEY_FRAME_SAMPLE, v.coerceAtLeast(1)).apply()

    /** 分割最低间隔（毫秒）。任务书第十九节：性能不足先降 AI 频率，目标 3–8 FPS。 */
    var segmentationMinIntervalMs: Long
        get() = sp.getLong(KEY_SEG_INTERVAL, 150L).coerceAtLeast(0L)
        set(v) = sp.edit().putLong(KEY_SEG_INTERVAL, v.coerceAtLeast(0L)).apply()

    /** 检测器最低间隔（毫秒）。任务书第十九节建议 5–10 FPS。 */
    var detectorMinIntervalMs: Long
        get() = sp.getLong(KEY_DETECTOR_INTERVAL, 150L).coerceAtLeast(0L)
        set(v) = sp.edit().putLong(KEY_DETECTOR_INTERVAL, v.coerceAtLeast(0L)).apply()

    fun summary(): String = buildString {
        append("arcore_owner=").append(arcoreOwner)
        append(" log=").append(sensorLog)
        append(" traversability=").append(traversabilityExperiment)
        append(" worldpath_shadow=").append(worldPathShadow)
        append(" safe_motion_shadow=").append(safeMotionShadow)
        append(" last10m=").append(last10mCapture)
        append(" frame_sample=").append(frameSampleEvery)
        append(" seg_ms=").append(segmentationMinIntervalMs)
        append(" det_ms=").append(detectorMinIntervalMs)
    }

    companion object {
        const val NAME = "shadow_flags"
        const val KEY_ARCORE = "ENABLE_ARCORE_OWNER"
        const val KEY_LOG = "ENABLE_SENSOR_LOG"
        const val KEY_TRAVERSABILITY = "ENABLE_TRAVERSABILITY_EXPERIMENT"
        const val KEY_WORLDPATH = "ENABLE_WORLDPATH_SHADOW"
        const val KEY_SAFE_MOTION = "ENABLE_SAFE_MOTION_SHADOW"
        const val KEY_LAST10M = "ENABLE_LAST10M_CAPTURE"
        const val KEY_FRAME_SAMPLE = "SHADOW_FRAME_SAMPLE_EVERY"
        const val KEY_SEG_INTERVAL = "SHADOW_SEG_MIN_INTERVAL_MS"
        const val KEY_DETECTOR_INTERVAL = "SHADOW_DETECTOR_MIN_INTERVAL_MS"
    }
}

/**
 * 单一 Camera Owner 仲裁。
 *
 * CameraActivity 用 CameraX 的 ProcessCameraProvider 绑定 DEFAULT_BACK_CAMERA；ARCore Session
 * 打开的是同一个物理后摄。两者同时开必然有一方拿不到相机（CameraActivity 里那条
 * "相机打不开，可能是没给权限或者被别的应用占了" 就是这个冲突的表现）。
 *
 * 第一版策略：影子链路启动前必须确认 CameraX 已经放开相机；拿不到就直接报告并拒绝启动，
 * 不使用 SharedCamera（SharedCamera 会限制硬件深度传感器的利用，且引入额外面/流管理）。
 */
object ArCameraOwnership {

    enum class State { FREE, CAMERA_ACTIVITY_HOLDING, SHADOW_RUNNING, UNKNOWN }

    /** 进程内影子链路占用标记。进程被杀 / force-stop 后自动归零，不会跨进程永久锁死。 */
    private val shadowHeld = java.util.concurrent.atomic.AtomicBoolean(false)

    fun claimShadow(): Boolean = shadowHeld.compareAndSet(false, true)

    fun releaseShadow() { shadowHeld.set(false) }

    fun shadowHeldNow(): Boolean = shadowHeld.get()

    /**
     * CameraX 侧现在是否还占着后摄。
     *
     * 时序要点：`frameGate.stop()` 在 CameraActivity.onStop 里**同步**把 cameraActive 置 false，
     * 但 CameraX 真正释放硬件要等它的 Lifecycle 走完，所以 FREE **不等于**"现在就能开 ARCore"。
     * probe() 只回答"有没有前台相机界面"；真正的抢占竞态由
     * `ArShadowEngine.resumeWithRetry()` 的退避重试兜住（CameraNotAvailableException 重试 10 次）。
     */
    fun probe(): State {
        if (shadowHeld.get()) return State.SHADOW_RUNNING
        val holder = CameraActivity.instance ?: return State.FREE
        return if (holder.cameraActive) State.CAMERA_ACTIVITY_HOLDING else State.FREE
    }

    /**
     * 请求生产相机界面退出。**不自动调用**：CameraActivity.onDestroy 里有
     * `TtsEngine.speak("相机已关")`，影子链路去 finish 它就等于在真实用户正在用的导航里
     * 插播一句并中断全部视觉引导。所以只提供能力，由调试屏提示工作人员先手动关看路。
     */
    fun requestCameraActivityRelease(): Boolean {
        val holder = CameraActivity.instance ?: return false
        holder.finish()
        return true
    }
}

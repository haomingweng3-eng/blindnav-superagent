package com.blindnav.app.shadow

import com.google.ar.core.Camera
import com.google.ar.core.Pose
import com.google.ar.core.TrackingState
import org.json.JSONObject

/**
 * 统一的 ARCore 位姿样本（任务书 4.2）。
 *
 * 坐标系：ARCore world frame 是重力对齐的，Y 轴向上，相机初始位置为原点，
 * 单位米，右手系。pose 是"相机在 world 中的位姿"，不是身体脚底位置——
 * 手持手机在胸前时，(tx,tz) 是手机的水平位置，脚的位置需要额外的外参标定，
 * 本阶段不做这个标定，因此 WorldPath 影子只用相对几何，不声称得到"脚的位置"。
 *
 * 注意 trackingState=PAUSED 时 tx/ty/tz 仍然是上一次的有效值，必须连同
 * trackingState 一起读，不能单独取坐标用。
 */
data class ArPoseSample(
    val arTimestampNanos: Long,
    val androidCameraTimestampNanos: Long,
    val receivedRtNanos: Long,
    val trackingState: String,
    val failureReason: String,
    val tx: Double,
    val ty: Double,
    val tz: Double,
    val qx: Double,
    val qy: Double,
    val qz: Double,
    val qw: Double,
    /** 航向：相机光轴（ARCore 相机 -Z 方向）在水平面上的投影，0=+X 逆时针，单位度。 */
    val cameraYawDegrees: Double?,
    val focalX: Float?,
    val focalY: Float?,
    val principalX: Float?,
    val principalY: Float?,
    val imageWidth: Int,
    val imageHeight: Int,
    /** 由 SensorClock 拟合出的 elapsedRealtime 基准时间；跨域不可比时为 null（不是 0，不是猜值）。 */
    val rtTimestampNanos: Long?
) {
    fun json(): Map<String, Any?> = linkedMapOf(
        "ar_ns" to arTimestampNanos,
        "cam_ns" to androidCameraTimestampNanos,
        "rt_ns" to receivedRtNanos,
        "rt_from_ar_ns" to rtTimestampNanos,
        "tracking" to trackingState,
        "failure" to failureReason,
        "tx" to tx, "ty" to ty, "tz" to tz,
        "qx" to qx, "qy" to qy, "qz" to qz, "qw" to qw,
        "yaw_deg" to cameraYawDegrees,
        "fx" to focalX, "fy" to focalY, "cx" to principalX, "cy" to principalY,
        "img_w" to imageWidth, "img_h" to imageHeight
    )

    companion object {
        fun of(frameTimestampNanos: Long, cameraTimestampNanos: Long, receivedRtNanos: Long,
               camera: Camera, pose: Pose, clock: SensorClock): ArPoseSample {
            val state = camera.trackingState
            val reason = runCatching { camera.trackingFailureReason.name }.getOrDefault("NONE")
            val yaw = if (state == TrackingState.TRACKING) {
                // 相机看向 -Z；用四元数把 (0,0,-1) 旋到世界系再取 atan2(-z, x)。
                val v = pose.rotateVector(floatArrayOf(0f, 0f, -1f))
                (Math.toDegrees(Math.atan2(-v[2].toDouble(), v[0].toDouble())) + 360.0) % 360.0
            } else null
            val intr = runCatching { camera.imageIntrinsics }.getOrNull()
            val focal = intr?.focalLength
            val pp = intr?.principalPoint
            val dim = intr?.imageDimensions
            return ArPoseSample(
                arTimestampNanos = frameTimestampNanos,
                androidCameraTimestampNanos = cameraTimestampNanos,
                receivedRtNanos = receivedRtNanos,
                trackingState = state.name,
                failureReason = reason,
                tx = pose.tx().toDouble(), ty = pose.ty().toDouble(), tz = pose.tz().toDouble(),
                qx = pose.qx().toDouble(), qy = pose.qy().toDouble(),
                qz = pose.qz().toDouble(), qw = pose.qw().toDouble(),
                cameraYawDegrees = yaw,
                focalX = focal?.getOrNull(0), focalY = focal?.getOrNull(1),
                principalX = pp?.getOrNull(0), principalY = pp?.getOrNull(1),
                imageWidth = dim?.getOrNull(0) ?: 0,
                imageHeight = dim?.getOrNull(1) ?: 0,
                rtTimestampNanos = clock.arToRt(frameTimestampNanos)
            )
        }
    }
}

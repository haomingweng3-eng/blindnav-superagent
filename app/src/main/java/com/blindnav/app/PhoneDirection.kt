package com.blindnav.app

import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.abs
import org.json.JSONObject

/** ENU旋转矩阵：手机顶部为+Y，后置镜头观察方向为-Z。二者均不是身体前进方向。 */
class PhoneDirection {
    data class Reading(val degrees: Float, val axis: String)
    data class FrameReading(val degrees: Float, val skewMs: Long, val spreadDegrees: Float, val upright: Boolean)
    private data class Sample(val matrix: FloatArray, var accuracy: Int, val at: Long, val error: Float?)
    private data class History(val at: Long, val sample: Sample?)
    private val cameraHistory = ArrayDeque<History>()
    private var absolute: Sample? = null
    private var relative: Sample? = null
    private var absoluteAt = Long.MIN_VALUE
    private var relativeAt = Long.MIN_VALUE

    @Synchronized fun update(matrix: FloatArray, isRelative: Boolean, accuracy: Int, at: Long, error: Float? = null) {
        if (at <= (if (isRelative) relativeAt else absoluteAt)) return
        if (isRelative) relativeAt = at else absoluteAt = at
        val sample = if (matrix.size == 9 && matrix.all { it.isFinite() }) Sample(matrix.copyOf(), accuracy, at, error) else null
        if (isRelative) relative = sample else absolute = sample
        if (!isRelative) {
            cameraHistory.addLast(History(at,sample?.copy()))
            while (cameraHistory.size>128 || (cameraHistory.size>1 && at-cameraHistory.first().at>5_000_000_000L))
                cameraHistory.removeFirst()
        }
    }
    @Synchronized fun accuracyChanged(isRelative: Boolean, accuracy: Int) {
        (if (isRelative) relative else absolute)?.accuracy = accuracy
        if (!isRelative && accuracy<2) cameraHistory.clear()
    }
    private fun fresh(s: Sample?, isRelative: Boolean, now: Long): Sample? {
        if (s == null || now - s.at !in 0L..2_000_000_000L || s.accuracy < (if (isRelative) 1 else 2)) return null
        if (!isRelative && s.error != null && (!s.error.isFinite() || s.error !in 0f..0.523599f)) return null
        return s
    }
    private fun direction(east: Float, north: Float, axis: String): Reading? {
        if (hypot(east, north) < 0.85f) return null
        return Reading(((Math.toDegrees(atan2(east, north).toDouble()) + 360) % 360).toFloat(), axis)
    }
    private fun camera(s: Sample): Reading? = direction(-s.matrix[2], -s.matrix[5], "camera")
    private fun preferred(s: Sample): Reading? {
        val m = s.matrix
        return when {
            m[8] >= 0.8f -> direction(m[1], m[4], "top")
            abs(m[8]) <= 0.5f -> camera(s)
            else -> null // 过渡姿态不切换参考轴并给出看似确定的角度。
        }
    }
    @Synchronized fun heading(now: Long): Reading? = fresh(absolute, false, now)?.let { preferred(it) }
    @Synchronized fun cameraHeading(now: Long): Reading? = fresh(absolute, false, now)?.let { camera(it) }
    @Synchronized fun cameraUpright(now: Long): Boolean = fresh(absolute,false,now)?.let {
        it.matrix[7]>=0.985f && camera(it)!=null
    } ?: false
    /** Only interpolate bracketed absolute camera-axis samples; never extrapolate current pose backwards. */
    @Synchronized fun cameraHeadingAt(exposureAt: Long): FrameReading? {
        val before=cameraHistory.lastOrNull { it.at<=exposureAt } ?: return null
        val after=cameraHistory.firstOrNull { it.at>=exposureAt } ?: return null
        val left=exposureAt-before.at;val right=after.at-exposureAt
        if (left !in 0L..150_000_000L || right !in 0L..150_000_000L) return null
        val a=before.sample?.let { fresh(it,false,it.at) }?.let { camera(it) } ?: return null
        val b=after.sample?.let { fresh(it,false,it.at) }?.let { camera(it) } ?: return null
        val delta=((b.degrees-a.degrees+540f)%360f)-180f
        if (abs(delta)>10f) return null
        val fraction=if(after.at==before.at) 0f else left.toFloat()/(after.at-before.at).toFloat()
        return FrameReading((a.degrees+delta*fraction+360f)%360f,
            (maxOf(left,right)+999_999L)/1_000_000L,abs(delta),
            before.sample!!.matrix[7]>=0.985f && after.sample!!.matrix[7]>=0.985f)
    }
    @Synchronized fun clearHistory() {
        cameraHistory.clear();absolute=null;relative=null;absoluteAt=Long.MIN_VALUE;relativeAt=Long.MIN_VALUE
    }
    @Synchronized fun gyroHeading(now: Long): Reading? = fresh(relative, true, now)?.let { preferred(it) }
    @Synchronized fun snapshot(now: Long): String {
        val a = heading(now); val r = gyroHeading(now); val c = cameraHeading(now)
        return JSONObject().put("absolute", a?.degrees ?: JSONObject.NULL).put("absoluteAxis", a?.axis ?: "")
            .put("relative", r?.degrees ?: JSONObject.NULL).put("relativeAxis", r?.axis ?: "")
            .put("camera", c?.degrees ?: JSONObject.NULL).toString()
    }
}

package com.blindnav.app.shadow

import android.content.Context
import android.graphics.Bitmap
import android.location.Location
import android.util.Log
import com.blindnav.app.last10m.CaptureLedger
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * 最后十米关系数据采集（任务书第 13/14 节 / 原第二十二、二十三节）。
 *
 * 本阶段**不追求入口 Recall**，只解决一件事：让真机能够方便地存下
 * POI → Sign → Door → Ground 这一组关系，并且这组关系**不会串到别的店、别的会话**。
 * 电脑阶段给不出入口识别能力，缺的就是这批真实关系数据，不是缺状态机。
 *
 * 这一版把账本逻辑挪进了 [CaptureLedger]（纯 Kotlin），这里只保留 Android 侧必需的三件事：
 *  1. 从 `Context` 拿可写目录；
 *  2. 把 `Bitmap` 压成 JPEG 字节；
 *  3. 把事件写进 logcat。
 *
 * 目录结构（每组一个）：
 *   Android/data/com.blindnav.nav/files/last10m/group-<UTC>/
 *     0001-wide_scan.jpg  0002-sign.jpg  …
 *     manifest.json     ← 组身份、session、missing_kinds、每条记录
 *     index.jsonl       ← 一行一条，脚本可直接读
 *
 * 只记研究需要的字段，不写任何个人标识；POI 名由操作者手输。
 */
class Last10mCaptureStore(
    context: Context,
    private val rootDir: File = File(context.getExternalFilesDir(null), "last10m").apply { mkdirs() },
    private val events: (String) -> Unit = { Log.w(TAG, it) }
) {

    private val ledger = CaptureLedger(
        root = rootDir,
        wallClockMs = { System.currentTimeMillis() },
        // 时长一律用单调时钟：墙上时钟回拨会把组时长算成负数（第 9 节）。
        monoClockMs = { android.os.SystemClock.elapsedRealtime() },
        events = events
    )

    /** 当前绑定的会话。没有绑定就写 `NO_SESSION`，让"这一段没有会话身份"成为可见事实。 */
    @Volatile private var sessionId: String? = null

    val root: File get() = rootDir

    fun bindSession(id: String?) {
        sessionId = id
        events("CAPTURE_SESSION_BOUND session=${id ?: CaptureLedger.NO_SESSION}")
    }

    fun boundSession(): String? = sessionId

    val activeGroup: String? get() = ledger.activeGroup()?.groupId

    /** 开始一组入口采集。返回组目录名；失败返回 null。 */
    fun startGroup(poi: String, location: Location?): String? {
        val name = ledger.startGroup(
            poi, sessionId,
            location?.latitude, location?.longitude,
            if (location == null) null else location.accuracy.toDouble()
        )
        if (name != null) { lastIndex = -1; lastKinds.clear() }
        return name
    }

    /**
     * 不依赖 ARCore 的采集入口。
     *
     * ARCore 路线暂停后，关系数据采集不能跟着停：这里只记 CameraX 帧号与罗盘朝向，
     * `pose_source` 明确写成 `compass_only_not_6dof`，不让读数据的人误以为有六自由度位姿。
     */
    fun captureCameraX(kind: String, bitmap: Bitmap, cameraFrameId: Long,
                       headingDegrees: Double?, callerSessionId: String? = sessionId):
            CaptureLedger.CaptureRecord? =
        writeImage(kind, bitmap, cameraFrameId, headingDegrees, callerSessionId,
            CaptureLedger.POSE_SOURCE, CaptureLedger.TRACKING, emptyMap())

    /** ARCore 分支（当前设备不被 ARCore 接受，保留是为了不锁死将来的可能）。 */
    fun capture(kind: String, bitmap: Bitmap, pose: ArPoseSample?, location: Location?,
                cameraFrameId: Long = -1L,
                callerSessionId: String? = sessionId): CaptureLedger.CaptureRecord? {
        val extras = LinkedHashMap<String, String>()
        if (pose != null) {
            extras["ar_ns"] = pose.arTimestampNanos.toString()
            extras["rt_ns"] = pose.receivedRtNanos.toString()
            extras["tx"] = pose.tx.toString(); extras["ty"] = pose.ty.toString()
            extras["tz"] = pose.tz.toString()
            extras["qx"] = pose.qx.toString(); extras["qy"] = pose.qy.toString()
            extras["qz"] = pose.qz.toString(); extras["qw"] = pose.qw.toString()
            extras["camera_yaw_deg"] = pose.cameraYawDegrees.toString()
        }
        if (location != null) {
            extras["lat"] = location.latitude.toString()
            extras["lng"] = location.longitude.toString()
            extras["accuracy_m"] = location.accuracy.toString()
        }
        return writeImage(kind, bitmap, cameraFrameId, location?.bearing?.toDouble(),
            callerSessionId,
            if (pose == null) CaptureLedger.POSE_SOURCE else "arcore_pose",
            pose?.trackingState ?: CaptureLedger.TRACKING, extras)
    }

    private fun writeImage(kind: String, bitmap: Bitmap, frameId: Long, headingDegrees: Double?,
                           callerSessionId: String?, poseSource: String, tracking: String,
                           extras: Map<String, String>): CaptureLedger.CaptureRecord? {
        val sid = callerSessionId ?: sessionId ?: CaptureLedger.NO_SESSION
        val bytes = runCatching {
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
            out.toByteArray()
        }.getOrElse {
            events("CAPTURE_FAILED kind=$kind reason=compress ${it.javaClass.simpleName}")
            return null
        }
        if (bytes.isEmpty()) {
            events("CAPTURE_FAILED kind=$kind reason=empty_jpeg")
            return null
        }
        val record = ledger.putImage(kind, bytes, frameId, sid, headingDegrees, poseSource, tracking, extras)
        if (record != null) {
            lastIndex = record.index
            lastKinds.add(kind)
        }
        return record
    }

    /**
     * 人工标注关系：研究模式下由看得见的人点选，这就是以后训练/评估要的标签。
     * [targetIndex] 缺省指向刚刚存下的那一张；一个组都没开就返回 null（不假装标注成功）。
     */
    fun markRelation(relation: String, targetIndex: Int = lastIndex,
                     callerSessionId: String? = sessionId): CaptureLedger.RelationRecord? =
        ledger.markRelation(relation,
            (callerSessionId ?: sessionId) ?: CaptureLedger.NO_SESSION,
            targetIndex)

    fun finishGroup(reason: String = "manual"): File? {
        val summary = ledger.finishGroup(reason) ?: return null
        lastIndex = -1
        return summary.directory
    }

    /** 上一张成功写入的序号，供关系标注指向。 */
    fun lastCaptureIndex(): Int = lastIndex

    fun groupCount(): Int = ledger.groupCount()

    /**
     * 面板用的一行采集状态。现场要能一眼回答：
     * 这组是不是当前会话的、已经存了几张、缺哪些 kind。
     */
    fun statusForPanel(sessionId: String?): String {
        val h = ledger.activeGroup() ?: return "capture=无进行中的组（先点「开始组」）"
        val mine = h.sessionId == (sessionId ?: h.sessionId)
        val present = kindsPresent()
        val missing = CaptureLedger.KINDS.filter { !present.contains(it) }
        return buildString {
            append("capture=${h.groupId} poi=${h.poiName.ifEmpty { "-" }} session=${h.sessionId}")
            append(if (mine) "" else " ← 会话不一致，本会话的图会被拒绝写入")
            appendLine()
            append("  已存 ${present.size}/${CaptureLedger.KINDS.size} kinds 缺=${missing.joinToString("/").ifEmpty { "-" }}")
        }
    }

    /** 当前组里已经存在哪些 kind（从本进程内成功写入的记录推出来）。 */
    fun kindsPresent(): Set<String> = lastKinds.toSet()

    private val lastKinds = LinkedHashSet<String>()

    /** 最近一次成功写入的序号，关系标注默认指向它。 */
    @Volatile private var lastIndex: Int = -1

    companion object {
        private const val TAG = "BlindNavLast10m"
        val KINDS = CaptureLedger.KINDS
        val RELATIONS = CaptureLedger.RELATIONS
    }
}

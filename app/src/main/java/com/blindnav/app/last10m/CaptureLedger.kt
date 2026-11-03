package com.blindnav.app.last10m

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 关系数据采集的账本（任务书第 13/14 节）。
 *
 * 这一层刻意**不碰 Android**：不 import `Bitmap`、不 import `org.json`、不用 `Context`。
 * 原因是电脑阶段必须能真的验证采集完整性，而不是等真机采完才发现一组数据里
 * A 店的招牌和 B 店的门混在同一组里。写 JPEG 由 Android 侧的适配器负责，
 * 这里只管：组、记录、会话归属、清单与缺失项。
 *
 * 三条硬要求：
 *  1. 每个组唯一确定 `groupId + sessionId + poiName + startTimestamp`；
 *  2. 每条图片记录带 `index / kind / frameId / timestamp / heading / poseSource`，
 *     关系标注带它指向的 `target_index`；
 *  3. 会话身份不一致 ⇒ 拒绝写入并记 `SESSION_MISMATCH`。宁可少一张图，
 *     也不能把两家店的关系拼成一组标签。
 *
 * 两种时钟语义分开（第 9 节）：`wallClockMs` 只用于写进文件的年月日；
 * 一切"过了多久"都用 `monoClockMs`，避免时钟回拨把组时长算成负数。
 */
class CaptureLedger(
    val root: File,
    private val wallClockMs: () -> Long = { System.currentTimeMillis() },
    // 默认值是 **单调**的（nanoTime），不是墙上时钟：直接构造时也不会掉回 PF-12 那个坑。
    // Android 侧仍然显式注入 `SystemClock.elapsedRealtime()`，两边语义一致。
    private val monoClockMs: () -> Long = { System.nanoTime() / 1_000_000L },
    private val events: (String) -> Unit = {}
) {

    data class GroupHeader(
        val groupId: String,
        val sessionId: String,
        val poiName: String,
        val startWallMs: Long,
        val startMonoMs: Long,
        val latitude: Double?,
        val longitude: Double?,
        val accuracyM: Double?
    )

    data class CaptureRecord(
        val index: Int,
        val kind: String,
        val file: String,
        val saved: Boolean,
        val frameId: Long,
        val capturedWallMs: Long,
        val headingDegrees: Double?,
        val poseSource: String,
        val tracking: String,
        val groupId: String,
        val sessionId: String,
        /** 附加字段（有 ARCore 位姿时写位姿；没有就空着，不编造）。 */
        val extras: Map<String, String> = emptyMap()
    )

    data class RelationRecord(
        val relation: String,
        val targetIndex: Int,
        val markedWallMs: Long,
        val groupId: String,
        val sessionId: String
    )

    data class GroupSummary(
        val header: GroupHeader,
        val captures: List<CaptureRecord>,
        val relations: List<RelationRecord>,
        val kindsPresent: Set<String>,
        val missingKinds: Set<String>,
        val durationMs: Long,
        val directory: File
    )

    enum class Reject { NO_ACTIVE_GROUP, SESSION_MISMATCH, WRITE_FAILED }

    private val stampFormat = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }

    private var header: GroupHeader? = null
    private var dir: File? = null
    private val captures = ArrayList<CaptureRecord>()
    private val relations = ArrayList<RelationRecord>()
    private var index = 0

    fun activeGroup(): GroupHeader? = header

    /**
     * 开一组。[sessionId] 为空也照实记成 `NO_SESSION`：
     * "没有会话身份"本身是一条要能被查出来的事实，不能偷偷编一个。
     */
    fun startGroup(poiName: String, sessionId: String?,
                   latitude: Double? = null, longitude: Double? = null,
                   accuracyM: Double? = null): String? {
        if (header != null) finishGroup("replaced")
        val wall = wallClockMs()
        val sid = sessionId ?: NO_SESSION
        val name = uniqueGroupName("group-${stampFormat.format(Date(wall))}")
        val groupDir = File(root, name)
        if (!groupDir.mkdirs()) {
            events("CAPTURE_FAILED reason=mkdir group=$name")
            return null
        }
        header = GroupHeader(name, sid, poiName, wall, monoClockMs(), latitude, longitude, accuracyM)
        dir = groupDir
        captures.clear(); relations.clear(); index = 0
        writeManifest("open", emptySet(), 0L, "open")
        events("CAPTURE_GROUP_START group=$name session=$sid poi=${poiName.take(24)} " +
            "started_utc=${stampFormat.format(Date(wall))}")
        return name
    }

    private fun uniqueGroupName(base: String): String {
        if (!File(root, base).exists()) return base
        var n = 1
        while (File(root, "$base-$n").exists()) n++
        return "$base-$n"
    }

    /**
     * 写一条图片记录。JPEG 字节由 Android 侧压好交进来。
     * 组不存在、或 [sessionId] 与当前组身份不符 ⇒ 拒绝，防止 A 店 sign 混进 B 店 group。
     */
    fun putImage(kind: String, jpegBytes: ByteArray, frameId: Long, sessionId: String,
                 headingDegrees: Double? = null,
                 poseSource: String = POSE_SOURCE,
                 tracking: String = TRACKING,
                 extras: Map<String, String> = emptyMap()): CaptureRecord? {
        val h = header ?: run {
            events("CAPTURE_FAILED reason=NO_ACTIVE_GROUP kind=$kind")
            return null
        }
        val d = dir ?: return null
        if (h.sessionId != sessionId) {
            events("CAPTURE_REJECTED reason=SESSION_MISMATCH kind=$kind group=${h.groupId} " +
                "groupSession=${h.sessionId} callerSession=$sessionId")
            return null
        }
        index += 1
        val fileName = "%04d-%s.jpg".format(index, kind)
        val file = File(d, fileName)
        val ok = runCatching { file.writeBytes(jpegBytes) }.isSuccess
        if (!ok) {
            index -= 1
            events("CAPTURE_FAILED group=${h.groupId} kind=$kind reason=write_io")
            return null
        }
        val record = CaptureRecord(index, kind, fileName, true, frameId, wallClockMs(),
            headingDegrees, poseSource, tracking, h.groupId, h.sessionId, extras)
        captures.add(record)
        writeManifest("open", emptySet(), 0L, "open")
        events("CAPTURE_SAVED group=${h.groupId} idx=${record.index} kind=$kind file=$fileName " +
            "frame=$frameId pose=$poseSource bytes=${jpegBytes.size}")
        return record
    }

    /** 人工关系标注。写不进（组不在/会话不符）就返回 null，并由事件说明原因。 */
    fun markRelation(relation: String, sessionId: String, targetIndex: Int = index): RelationRecord? {
        val h = header ?: run {
            events("CAPTURE_FAILED reason=NO_ACTIVE_GROUP relation=$relation")
            return null
        }
        if (h.sessionId != sessionId) {
            events("CAPTURE_REJECTED reason=SESSION_MISMATCH relation=$relation " +
                "groupSession=${h.sessionId} callerSession=$sessionId")
            return null
        }
        val rec = RelationRecord(relation, targetIndex, wallClockMs(), h.groupId, h.sessionId)
        relations.add(rec)
        writeManifest("open", emptySet(), 0L, "open")
        events("RELATION_MARKED group=${h.groupId} relation=$relation target_index=$targetIndex")
        return rec
    }

    /** 没有位姿时也要写清"只有罗盘"，不能空着让人误以为有六自由度。 */
    fun finishGroup(reason: String = "manual"): GroupSummary? {
        val h = header ?: return null
        val d = dir ?: return null
        val present = captures.map { it.kind }.toSet()
        val missing = KINDS.toSet() - present
        val duration = (monoClockMs() - h.startMonoMs).coerceAtLeast(0L)
        writeManifest("closed", missing, duration, reason)
        writeIndexFile()
        events("CAPTURE_GROUP_END group=${h.groupId} reason=$reason captures=${captures.size} " +
            "relations=${relations.size} missing=${missing.joinToString("/").ifEmpty { "-" }} " +
            "duration_ms=$duration")
        val summary = GroupSummary(h, captures.toList(), relations.toList(), present, missing, duration, d)
        header = null; dir = null
        return summary
    }

    /**
     * manifest.json 一次写全：组身份、缺失项、每条图片记录、每条关系标注。
     * 读数据的人不需要猜"这组里为什么没有 neighboring_door"——missing_kinds 直接说了。
     */
    private fun writeManifest(status: String, missing: Set<String>, durationMs: Long, reason: String) {
        val h = header ?: return
        val d = dir ?: return
        val sb = StringBuilder()
        sb.append("{\n")
        line(sb, "groupId", js(h.groupId))
        line(sb, "session_id", js(h.sessionId))
        line(sb, "poi_name", js(h.poiName))
        line(sb, "schema", js(SCHEMA))
        line(sb, "status", js(status))
        line(sb, "started_utc", js(stampFormat.format(Date(h.startWallMs))))
        line(sb, "start_epoch_ms", num(h.startWallMs))
        line(sb, "duration_ms", num(durationMs))
        line(sb, "close_reason", js(reason))
        line(sb, "pose_source", js(POSE_SOURCE))
        line(sb, "tracking", js(TRACKING))
        line(sb, "location", "{\"lat\": ${numOr(h.latitude)}, \"lng\": ${numOr(h.longitude)}, " +
            "\"accuracy_m\": ${numOr(h.accuracyM)}}")
        line(sb, "kinds_present",
            captures.map { js(it.kind) }.distinct().joinToString(", ", "[", "]"))
        line(sb, "missing_kinds", missing.joinToString(", ", "[", "]") { js(it) })
        line(sb, "image_count", num(captures.size.toLong()))
        line(sb, "relation_count", num(relations.size.toLong()))
        sb.append("  \"captures\": [\n")
        captures.forEachIndexed { i, c ->
            sb.append("    {").append(pairs(
                "index" to num(c.index.toLong()),
                "kind" to js(c.kind),
                "file" to js(c.file),
                "saved" to "true",
                "frame_id" to num(c.frameId),
                "captured_utc" to js(stampFormat.format(Date(c.capturedWallMs))),
                "captured_epoch_ms" to num(c.capturedWallMs),
                "camera_heading_deg" to numOr(c.headingDegrees),
                "pose_source" to js(c.poseSource),
                "tracking" to js(c.tracking),
                "group_id" to js(c.groupId),
                "session_id" to js(c.sessionId),
                "extras" to obj(c.extras)
            )).append("}")
            if (i != captures.lastIndex) sb.append(",")
            sb.append("\n")
        }
        sb.append("  ],\n")
        sb.append("  \"relations\": [\n")
        relations.forEachIndexed { i, r ->
            sb.append("    {").append(pairs(
                "relation" to js(r.relation),
                "target_index" to num(r.targetIndex.toLong()),
                "marked_utc" to js(stampFormat.format(Date(r.markedWallMs))),
                "group_id" to js(r.groupId),
                "session_id" to js(r.sessionId)
            )).append("}")
            if (i != relations.lastIndex) sb.append(",")
            sb.append("\n")
        }
        sb.append("  ]\n}\n")
        runCatching { File(d, "manifest.json").writeText(sb.toString()) }
            .onFailure {
                events("CAPTURE_FAILED reason=manifest_io group=${h.groupId} err=${it.javaClass.simpleName}")
            }
    }

    /** index.jsonl：一行一条，下游脚本不用解整份 manifest 也能读。 */
    private fun writeIndexFile() {
        val d = dir ?: return
        val sb = StringBuilder()
        for (c in captures) {
            sb.append("{").append(pairs(
                "index" to num(c.index.toLong()),
                "kind" to js(c.kind),
                "file" to js(c.file),
                "frame_id" to num(c.frameId),
                "captured_epoch_ms" to num(c.capturedWallMs),
                "camera_heading_deg" to numOr(c.headingDegrees),
                "pose_source" to js(c.poseSource),
                "group_id" to js(c.groupId),
                "session_id" to js(c.sessionId)
            )).append("}\n")
        }
        for (r in relations) {
            sb.append("{").append(pairs(
                "relation" to js(r.relation),
                "target_index" to num(r.targetIndex.toLong()),
                "marked_epoch_ms" to num(r.markedWallMs),
                "group_id" to js(r.groupId),
                "session_id" to js(r.sessionId)
            )).append("}\n")
        }
        runCatching { File(d, "index.jsonl").writeText(sb.toString()) }
    }

    fun groupCount(): Int {
        val files = runCatching { root.listFiles() }.getOrNull() ?: return 0
        return files.count { it.isDirectory && it.name.startsWith("group-") }
    }

    private fun line(sb: StringBuilder, key: String, value: String) {
        sb.append("  \"").append(key).append("\": ").append(value).append(",\n")
    }

    private fun pairs(vararg items: Pair<String, String>): String =
        items.joinToString(", ") { (k, v) -> "\"$k\": $v" }

    private fun js(s: String): String = "\"" + s.replace("\\", "\\\\")
        .replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "") + "\""

    private fun num(v: Long): String = v.toString()

    private fun numOr(v: Any?): String = v?.toString() ?: "null"

    private fun obj(items: Map<String, String>): String =
        if (items.isEmpty()) "{}" else items.entries.joinToString(", ", "{", "}") {
            "\"${it.key}\": ${js(it.value)}"
        }

    companion object {
        const val SCHEMA = "last10m-capture/2"
        const val NO_SESSION = "NO_SESSION"
        /** ARCore 路线已暂停：只有罗盘，没有六自由度位姿，这条必须写在数据里。 */
        const val POSE_SOURCE = "compass_only_not_6dof"
        const val TRACKING = "NO_ARCORE_POSE"
        val KINDS = listOf("wide_scan", "sign", "candidate_door", "neighboring_door", "door_front_ground")
        val RELATIONS = listOf(
            "correct_sign", "correct_door", "neighboring_wrong_door",
            "ground_same_entrance", "step", "curb", "obstruction"
        )
    }
}

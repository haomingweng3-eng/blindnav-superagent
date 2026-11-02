package com.blindnav.app.last10m

/**
 * 入口候选与邻门冲突（任务书第九节）。
 *
 * 这一节是整个功能里最容易出"自信的错误"的地方：Frame A 拍到星巴克招牌，
 * Frame B 拍到隔壁银行的门，如果系统只按"最近看到一个门状物"来关联，
 * 就会把隔壁门报成目标入口——这正是优先级最高的 False Acceptance。
 *
 * 没有位姿可用（ARCore 已暂停）时，跨帧"同一扇门"的判据只剩画面位置连续性，
 * 所以这里刻意做得很保守：
 *  - 只有在**相邻的少量帧内**、且框体重叠达到门限，才认为是同一扇门；
 *  - 否则一律发新的 doorId，宁可当成"另一扇候选门"也不合并；
 *  - 一旦目标锁失效，所有已建立的关系**全部作废**（case 5 的落点）；
 *  - 同一帧里出现两个以上候选门 → 记 neighbor_conflict，必须 REOBSERVE 而不是挑一个。
 */
class EntranceTracker(
    private val iouThreshold: Double = 0.35,
    private val maxGapFrames: Long = 6,
    private val maxGapMs: Long = 1500L,
    /**
     * 稀疏交付（云端 VLM / 回放）之间允许的连续时间。
     *
     * 一次视觉回答要 1.5–2.5 s 才回来，用逐帧的 1500 ms 窗去归并，
     * 每一次回答都会把同一扇门当成新门：`seenFrames` 永远是 1，
     * 刚挂上去的关系也永远挂在一个刚出生的候选上。
     * 这里放宽的是**归并窗**，不是"旧门能活多久"——
     * 一份回答覆盖了当前画面里所有门，没被再报告的门由 [expireNotReported] 当场退出，
     * 所以放宽归并窗并不会让早已离开画面的门继续参与判定。
     * 残余风险要写明白：连续两次回答里，镜头甩到隔壁但画面位置接近时可能被并成同一扇；
     * 这一条由 `neighborConflict`/关系证据过期兜底，真机验证时专门测。
     */
    private val sparseMaxGapMs: Long = 5000L
) {

    /** 门候选的节奏：逐帧检测，还是秒级的整幅画面回答。 */
    enum class DoorCadence { FRAME_LEVEL, SPARSE_REPORT }

    private data class sighting(val frameId: Long, val tsMs: Long, val box: IntArray)

    /** 事件回调。生产里接到 logcat，测试里收集成列表——"日志是否够用"要能被断言。 */
    var listener: ((String) -> Unit)? = null

    private val candidates = LinkedHashMap<String, EntranceCandidate>()
    private val recent = HashMap<String, ArrayDeque<sighting>>()
    private var sequence = 0
    private var activeDoorId: String? = null

    fun candidates(): List<EntranceCandidate> = candidates.values.toList()

    fun active(): EntranceCandidate? = activeDoorId?.let { candidates[it] }

    /**
     * 记录一次门/入口框检测。返回它归到的 doorId。
     * 归并失败就新建候选，绝不"顺手"接到旧门上。
     *
     * `source` / `debugPosition` 必须写清楚来源：人工按出来的框是
     * `DEBUG_MANUAL_PROPOSAL`，不能和真实门检测混成同一种证据。
     */
    fun observeDoor(left: Int, top: Int, right: Int, bottom: Int,
                    frameId: Long, tsMs: Long, nowMs: Long,
                    source: CandidateSource = CandidateSource.UNKNOWN,
                    debugPosition: DebugDoorPosition? = null,
                    sessionId: String? = null,
                    cadence: DoorCadence = DoorCadence.FRAME_LEVEL): String {
        val box = intArrayOf(left, top, right, bottom)
        val existing = candidates.keys.firstOrNull { id ->
            val hist = recent[id] ?: return@firstOrNull false
            val last = hist.lastOrNull() ?: return@firstOrNull false
            val frameGap = frameId - last.frameId
            val timeGap = ageMs(nowMs, last.tsMs)
            val overlap = iou(last.box, box) >= iouThreshold
            overlap && if (cadence == DoorCadence.SPARSE_REPORT) timeGap <= sparseMaxGapMs
            else frameGap in 0..maxGapFrames && timeGap <= maxGapMs
        }
        val id = existing ?: "door-${++sequence}"
        val candidate = candidates[id] ?: EntranceCandidate(
            doorId = id, left = left, top = top, right = right, bottom = bottom,
            firstSeenMs = tsMs, lastSeenMs = tsMs, seenFrames = 0,
            sourceFrameId = frameId, source = source, debugPosition = debugPosition,
            sessionId = sessionId
        ).also {
            candidates[id] = it
            recent[id] = ArrayDeque()
        }
        candidate.left = left; candidate.top = top; candidate.right = right; candidate.bottom = bottom
        candidate.sourceFrameId = frameId
        candidate.source = source
        candidate.debugPosition = debugPosition
        if (tsMs >= candidate.lastSeenMs) candidate.lastSeenMs = tsMs
        val firstTime = !recent.getValue(id).any { it.frameId == frameId }
        if (firstTime) candidate.seenFrames += 1
        recent.getValue(id).addLast(sighting(frameId, tsMs, box))
        while (recent.getValue(id).size > 12) recent.getValue(id).removeFirst()
        if (existing == null) activeDoorId = id
        emit(if (existing == null) "DOOR_ADDED" else "DOOR_UPDATED",
            "door=$id src=$source pos=${debugPosition ?: "-"} frame=$frameId " +
                "box=$left,$top,$right,$bottom seen=${candidate.seenFrames}")
        if (candidates.size >= 2) {
            emit("MULTIPLE_DOORS", "count=${candidates.size} ids=${candidates.keys.joinToString("/")}")
        }
        return id
    }

    /** 目标锁没了 → 关系全部作废。这是 case 5 的硬要求。 */
    fun invalidateRelations(reason: String) {
        val affected = candidates.values.filter { it.relationConfirmed != null }.map { it.doorId }
        for (c in candidates.values) {
            c.relationConfirmed = null
            c.relationSource = null
        }
        activeDoorId = null
        lastInvalidateReason = reason
        if (affected.isNotEmpty()) {
            emit("RELATION_INVALIDATED", "reason=$reason doors=${affected.joinToString("/")}")
        }
    }

    var lastInvalidateReason: String? = null
        private set

    /** 同一视野里是否同时存在多个候选门（邻门场景）。 */
    fun neighborConflict(nowMs: Long, freshMs: Long = 1500L): Boolean =
        freshCount(nowMs, freshMs) >= 2

    /**
     * 检测器语义的过期：这一帧没再看到的候选，就不再算"眼前有的门"。
     *
     * 人走两步、镜头一转，刚才那扇就已经不在画面里了；如果它还在候选表里活着，
     * 系统会继续拿它制造"有多个入口"的困惑，甚至拿它的旧关系去确认新的门。
     * 人工声明的候选不参与：那本来就不是检测输出，是人给的标签。
     *
     * @param seenThisFrame 这一帧报告过的内部 doorId 集合
     * @return 被退出的 doorId
     */
    fun expireNotReported(seenThisFrame: Set<String>, frameId: Long, nowMs: Long): List<String> {
        val stale = candidates.values.filter {
            it.source != CandidateSource.DEBUG_MANUAL_PROPOSAL && it.doorId !in seenThisFrame
        }.map { it.doorId }
        for (k in stale) {
            candidates.remove(k); recent.remove(k)
            emit("DOOR_EXPIRED", "door=$k reason=not_seen_in_frame frame=$frameId now=$nowMs")
        }
        if (activeDoorId != null && !candidates.containsKey(activeDoorId)) activeDoorId = null
        return stale
    }

    /**
     * 还"看得见"的候选门数量。
     *
     * 单独要这一数，是因为 `candidates.size` 会把早就离开画面的旧门也算进来 ——
     * 拿 size 当"多门分不清"的依据，会让人走远了还在被提示"有多个入口"。
     */
    fun freshCount(nowMs: Long, freshMs: Long = 1500L): Int =
        candidates.values.count { ageMs(nowMs, it.lastSeenMs) <= freshMs }

    /** 只有一扇门被确认过关系，但当前活跃候选是另一扇 → 冲突。 */
    fun relationConflict(activeDoorId: String?): Boolean {
        if (activeDoorId == null) return false
        val confirmed = candidates.values.filter { it.relationConfirmed == true }
        if (confirmed.isEmpty()) return false
        return confirmed.none { it.doorId == activeDoorId }
    }

    /**
     * 把活跃候选切到指定门。
     *
     * 为什么需要：视觉一帧里可能同时给两扇门，`observeDoor` 会把活跃候选留在最后看到的那一扇。
     * 一旦其中一扇被判定"属于目标"，注意力的落点就应该跟着换过去 ——
     * 否则门前地面等证据会挂在隔壁那扇上，整条链就在错误的门往下走。
     */
    fun setActive(doorId: String, reason: String): Boolean {
        val c = candidates[doorId] ?: return false
        if (activeDoorId == doorId) return false
        activeDoorId = doorId
        emit("DOOR_ACTIVATED", "door=$doorId reason=$reason box=${c.left},${c.top},${c.right},${c.bottom}")
        return true
    }

    fun confirmRelation(doorId: String, source: EvidenceSource, sameStorefront: Boolean,
                        evidenceMs: Long = 0L) {
        val c = candidates[doorId] ?: return
        val before = c.relationConfirmed
        c.relationConfirmed = sameStorefront
        c.relationSource = source
        c.relationEvidenceMs = evidenceMs
        emit(if (sameStorefront) "RELATION_CONFIRMED" else "RELATION_CONFLICT",
            "door=$doorId src=${source.label()} prev=${before ?: "null"} frame=${c.sourceFrameId} " +
                "evidenceMs=$evidenceMs")
    }

    fun attachGround(doorId: String, ground: GroundEvidence) {
        val c = candidates[doorId] ?: return
        c.groundEvidence = ground
        c.groundObserved = ground.visible
        emit("GROUND_EVIDENCE", "door=$doorId visible=${ground.visible} src=${ground.source} " +
            "step=${ground.step} curb=${ground.curb} obstruction=${ground.obstruction} frame=${ground.sourceFrameId}")
    }

    /** 丢弃太久没再看到的候选，避免旧门长期污染判断。返回被丢弃的 doorId。 */
    fun prune(nowMs: Long, ttlMs: Long = 6000L): List<String> {
        val stale = candidates.filterValues { ageMs(nowMs, it.lastSeenMs) > ttlMs }.keys.toList()
        for (k in stale) {
            candidates.remove(k); recent.remove(k)
            emit("DOOR_EXPIRED", "door=$k ttl=${ttlMs}ms now=$nowMs")
        }
        if (activeDoorId != null && !candidates.containsKey(activeDoorId)) activeDoorId = null
        return stale
    }

    fun reset() {
        candidates.clear(); recent.clear(); activeDoorId = null; sequence = 0
    }

    private fun emit(event: String, detail: String) {
        listener?.invoke("$event $detail")
    }

    private fun iou(a: IntArray, b: IntArray): Double {
        val ix = maxOf(0, minOf(a[2], b[2]) - maxOf(a[0], b[0])).toDouble()
        val iy = maxOf(0, minOf(a[3], b[3]) - maxOf(a[1], b[1])).toDouble()
        val inter = ix * iy
        val areaA = (a[2] - a[0]) * (a[3] - a[1])
        val areaB = (b[2] - b[0]) * (b[3] - b[1])
        val union = areaA + areaB - inter.toInt()
        return if (union <= 0) 0.0 else inter / union
    }
}

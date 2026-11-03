package com.blindnav.app.last10m

/**
 * 目标身份锁定（任务书第七节）。
 *
 * 禁止"单帧看到一次招牌就锁目标"。这里给两条合法路径：
 *  A. 同一来源在窗口内命中 ≥ [requiredHits] 次，且至少跨 [requiredFrames] 个不同帧；
 *  B. OCR 与 VLM 两路**独立**证据在窗口内一致（各 1 次即可）。
 * 两条都不满足就保持 SEARCH_TARGET。
 *
 * 另外两件事必须成立，否则这条锁就是假的：
 *  - 锁定后若目标连续消失超过 [lostTimeoutMs]，锁**自动失效**并清空计数（对应 case 5：
 *    目标消失后看到别的门，绝不能拿旧锁去关联新门）；
 *  - FUZZY 档单独出现**不计入**锁定（见 [TargetPoiProfile.acceptsAsIdentity]），
 *    模糊匹配只能作为佐证，不能独立确立身份。
 */
class TargetLock(
    private val profile: TargetPoiProfile,
    private val requiredHits: Int = 2,
    private val requiredFrames: Int = 2,
    private val windowMs: Long = 4000L,
    private val lostTimeoutMs: Long = 2500L
) {
    enum class Status { SEARCH_TARGET, TARGET_LOCKED, TARGET_LOST }

    data class Snapshot(
        val status: Status,
        val hitCount: Int,
        val distinctFrames: Int,
        val firstSeenMs: Long,
        val lastSeenMs: Long,
        val score: Double,
        val matchedAlias: String,
        val sources: Set<EvidenceSource>,
        val bbox: IntArray?,
        /** 区分"从没确认过目标"与"确认过但丢了"：两者的提示语和状态都不同。 */
        val everLocked: Boolean,
        /** 最后一次命中的档位与别名来源，面板要能回答"凭什么说这是目标"。 */
        val lastKind: TargetMatchKind = TargetMatchKind.NONE,
        val lastAliasSource: AliasSource? = null,
        /** 只算佐证的命中次数（过短派生别名 / 名字碎片 / 模糊档）。它们不参与锁定计数。 */
        val supportingHits: Int = 0,
        val supportingAlias: String = ""
    )

    private var hits = 0
    private var firstSeenMs = -1L
    private var lastSeenMs = -1L
    private var score = 0.0
    private var matchedAlias = ""
    private var locked = false
    private var everLocked = false
    private var lastKind = TargetMatchKind.NONE
    private var lastAliasSource: AliasSource? = null
    private var supportingHits = 0
    private var supportingAlias = ""
    private val frameIds = LinkedHashSet<Long>()
    private val sourceSet = LinkedHashSet<EvidenceSource>()
    private var bbox: IntArray? = null

    val status: Status get() = if (locked) Status.TARGET_LOCKED
        else if (hits > 0 && lastSeenMs >= 0) Status.SEARCH_TARGET else Status.SEARCH_TARGET

    fun isLocked(): Boolean = locked

    /** 喂入一次文字命中。返回喂完之后的状态快照。 */
    fun observe(hit: TextHit, nowMs: Long): Snapshot {
      return observeHit(profile.match(hit.text), hit.source, hit.sourceFrameId,
          intArrayOf(hit.left, hit.top, hit.right, hit.bottom), hit.timestampMs, nowMs)
    }

    /** VLM 报出来的身份走同一条确认路径，但来源标记不同，因此能构成"独立证据"。 */
    fun observeVlmIdentity(identityText: String, frameId: Long, tsMs: Long, nowMs: Long): Snapshot {
        return observeHit(profile.match(identityText), EvidenceSource.VLM, frameId, null, tsMs, nowMs)
    }

    private fun observeHit(
        match: TargetMatch?,
        source: EvidenceSource,
        frameId: Long,
        box: IntArray?,
        eventMs: Long,
        nowMs: Long
    ): Snapshot {
        expireIfStale(nowMs)
        if (match == null || match.kind == TargetMatchKind.NONE) {
            return snapshot()
        }
        // FUZZY 与 SUPPORTING 都不算身份证据：只加分、只记账，不进锁定计数。
        val countsForLock = profile.acceptsAsIdentity(match)
        if (eventMs > lastSeenMs) lastSeenMs = eventMs
        if (firstSeenMs < 0) firstSeenMs = eventMs
        bbox = box ?: bbox
        lastKind = match.kind
        lastAliasSource = match.aliasSource
        if (!countsForLock) {
            score = maxOf(score, match.score)
            if (match.kind == TargetMatchKind.SUPPORTING && !frameIds.contains(frameId)) {
                supportingHits += 1
                supportingAlias = match.matchedAlias
            }
            return snapshot()
        }
        if (!frameIds.contains(frameId)) {
            frameIds.add(frameId)
            hits += 1
        }
        sourceSet.add(source)
        score = maxOf(score, match.score)
        matchedAlias = match.matchedAlias
        if (!locked) {
            val multiFrame = hits >= requiredHits && frameIds.size >= requiredFrames
            val twoSources = sourceSet.size >= 2 && hits >= 2
            if (multiFrame || twoSources) { locked = true; everLocked = true }
        }
        return snapshot()
    }

    /** 目标丢失：清计数、清锁。之后即使看到门也不允许沿用旧候选关系。 */
    private fun expireIfStale(nowMs: Long) {
        if (lastSeenMs < 0) return
        val stale = ageMs(nowMs, lastSeenMs)
        if (stale > (if (locked) lostTimeoutMs else windowMs)) {
            clearEvidence()
            locked = false
        }
    }

    /**
     * 证据计数清零。佐证据也一起清——"佐证"是这一段画面里的佐证，
     * 不是永久有效的身世说明；留着它会让面板显示一个早就看不见的碎片词。
     */
    private fun clearEvidence() {
        hits = 0
        frameIds.clear()
        sourceSet.clear()
        firstSeenMs = -1L
        lastSeenMs = -1L
        score = 0.0
        matchedAlias = ""
        bbox = null
        lastKind = TargetMatchKind.NONE
        lastAliasSource = null
        supportingHits = 0
        supportingAlias = ""
    }

    fun reset() { clearEvidence(); locked = false; everLocked = false }

    /**
     * 读取时也要做过期检查。只在写入时检查的话，目标消失后锁会一直挂着，
     * 于是"旧目标锁 + 新看到的门"就能拼出假确认——这正是要防的那类错误。
     */
    fun snapshotAt(nowMs: Long): Snapshot {
        expireIfStale(nowMs)
        return snapshot()
    }

    /** 刚发生一次过期（locked→expired）。调用方据此发 TARGET_EXPIRED 事件。 */
    fun wasLockedButExpired(nowMs: Long): Boolean {
        val was = locked
        expireIfStale(nowMs)
        return was && !locked
    }

    fun snapshot(): Snapshot = Snapshot(
        status = if (locked) Status.TARGET_LOCKED else Status.SEARCH_TARGET,
        hitCount = hits,
        distinctFrames = frameIds.size,
        firstSeenMs = firstSeenMs,
        lastSeenMs = lastSeenMs,
        score = score,
        matchedAlias = matchedAlias,
        sources = sourceSet.toSet(),
        bbox = bbox?.copyOf(),
        everLocked = everLocked,
        lastKind = lastKind,
        lastAliasSource = lastAliasSource,
        supportingHits = supportingHits,
        supportingAlias = supportingAlias
    )
}

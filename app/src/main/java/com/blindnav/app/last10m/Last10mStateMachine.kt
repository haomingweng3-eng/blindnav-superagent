package com.blindnav.app.last10m

/**
 * 最后十米状态机（任务书第六节）。
 *
 * 九个状态，全部由**确定性规则**推进，VLM 与 OCR 都只是喂进来的证据：
 *
 *   APPROACH            还没进入目的地附近
 *   SEARCH_TARGET       已靠近，目标身份未确认
 *   TARGET_LOCKED       身份刚确认、尚未看到任何入口候选
 *   SEARCH_ENTRANCE     已确认身份，正在找门（有候选但当前无活跃候选）
 *   ENTRANCE_CANDIDATE  有活跃入口候选，关系还没开始判定
 *   VERIFY_RELATION     关系判定进行中/结果为 UNKNOWN
 *   VERIFY_GROUND       关系已确认，门前地面还没看到
 *   ARRIVAL_CONFIRMED   [ArrivalAuthority] 六条件齐备
 *   REOBSERVE           任何关系/新鲜度丢失
 *
 * 两条不可协商的原则：
 *  - 看到门 ≠ 到达；看到招牌 ≠ 找到入口。所以 TARGET_LOCKED 与 ARRIVAL_CONFIRMED 之间
 *    必须经过 relation 与 ground 两道独立确认。
 *  - 目标锁一旦失效，[EntranceTracker.invalidateRelations] 必须被调用，
 *    否则会出现"旧目标 + 新门 = 假确认"。
 */
enum class Last10mState {
    APPROACH, SEARCH_TARGET, TARGET_LOCKED, SEARCH_ENTRANCE, ENTRANCE_CANDIDATE,
    VERIFY_RELATION, VERIFY_GROUND, ARRIVAL_CONFIRMED, REOBSERVE
}

class Last10mStateMachine(
    val profile: TargetPoiProfile,
    private val lock: TargetLock = TargetLock(profile),
    private val tracker: EntranceTracker = EntranceTracker(),
    private val authority: ArrivalAuthority = ArrivalAuthority(),
    private val scanCoach: ScanCoach = ScanCoach(),
    /** 支持性/互斥地标由 POI 记录或用户给，不从店名自动派生（见 LandmarkEvidence 注释）。 */
    private val landmarkIndex: LandmarkIndex = LandmarkIndex(profile),
    /**
     * 会话身份。跨会话证据必须能被**裁决层**拒绝，而不是靠"每台机器一个新对象"
     * 这种约定。默认 null = 单会话场景，行为与从前一致。
     */
    private val sessionId: String? = null
) {

    data class Result(
        val state: Last10mState,
        val decision: ArrivalDecision,
        val prompt: String,
        val scanDirection: ScanDirection,
        val targetLocked: Boolean,
        val targetHits: Int,
        val activeDoorId: String?,
        val candidateCount: Int,
        val groundObserved: Boolean,
        val nextView: String?,
        val violations: List<String>,
        /** 能力 7：下一步该看哪里。完全由缺失的证据决定，VLM 无权生成。 */
        val needView: NeedView = NeedView.NONE,
        /** 能力 12：为什么现在不能确认。语音不念 code，调试面板念。 */
        val reasonCodes: List<ReasonCode> = listOf(ReasonCode.NONE),
        val viewPrompt: String = "",
        /** 方向为 UNKNOWN 时的原因，面板用来区分"没接高德"和"ScanCoach 坏了"。 */
        val scanReason: ScanUnknownReason = ScanUnknownReason.NONE,
        /** 门前地面的状态。人工长按只会得到 OBSERVED_MANUAL，不会是 SAFE/CLEAR。 */
        val groundState: GroundState = GroundState.NOT_OBSERVED,
        val groundSource: GroundSource = GroundSource.UNKNOWN
    )

    /** 事件出口。生产接 logcat，测试收集成列表。 */
    var events: ((String) -> Unit)? = null

    @Volatile private var inRange = false
    @Volatile private var previousState: Last10mState = Last10mState.APPROACH
    @Volatile private var lastFacts: VlmFacts? = null
    @Volatile private var lastScan: ScanDirection = ScanDirection.UNKNOWN
    @Volatile private var lastScanReason: ScanUnknownReason = ScanUnknownReason.ROUTE_BEARING_MISSING
    @Volatile private var blockingConflict = false
    @Volatile private var previousDecision: ArrivalDecision? = null
    @Volatile private var previousNeedView: NeedView? = null

    fun state(): Last10mState = previousState
    fun candidates(): List<EntranceCandidate> = tracker.candidates()
    fun targetSnapshot(): TargetLock.Snapshot = lock.snapshot()
    fun sessionId(): String? = sessionId

    // ---- 能力 1 / 2 / 3 / 7 / 12 的挂载点 ------------------------------------
    /** 扫描方向带迟滞与最短驻留，避免"左-前-右"来回翻。 */
    fun scanDirection(): ScanDirection = lastScan
    fun activeDoorId(): String? = tracker.active()?.doorId
    fun neighborConflict(nowMs: Long): Boolean = tracker.neighborConflict(nowMs)
    fun relationConflict(): Boolean = tracker.relationConflict(tracker.active()?.doorId)
    fun landmarkConflict(): Boolean = landmarks.conflict(clockNow())
    fun addressConflict(): Boolean = lastAddress == AddressOutcome.CONFLICT

    private val landmarks: LandmarkLedger = LandmarkLedger()
    private var addressExpectation: AddressEvidence = AddressEvidence()
    @Volatile private var lastAddress: AddressOutcome = AddressOutcome.UNKNOWN
    /** 最近一次 evaluate 的时间，给不传 nowMs 的访问器兜底用。 */
    @Volatile private var lastNowMs: Long = 0L
    private fun clockNow(): Long = lastNowMs

    init {
        // 候选层的事件直接转发，不套第二层前缀：日志里就是要能 grep "DOOR_ADDED"。
        tracker.listener = { line -> emitRaw(line) }
    }

    /** POI 有门牌/楼号时才注入；没有就整路证据退出，不会产生假冲突。 */
    fun setAddressExpectation(a: AddressEvidence) {
        addressExpectation = a
        emit("ADDRESS_EXPECT", if (a.empty) "none" else a.toString())
    }
    fun addressOutcome(): AddressOutcome = lastAddress

    /** 方向为 UNKNOWN 的原因。面板靠这一项区分"没接高德"和"ScanCoach 坏了"。 */
    fun scanUnknownReason(): ScanUnknownReason = lastScanReason

    /**
     * 当前活跃候选（含其地面证据）是否属于本会话。
     * 单会话（两边都是 null）永远为 true；一旦带上别的会话身份就是 false。
     */
    fun evidenceBelongsToSession(active: EntranceCandidate?): Boolean {
        if (active == null) return true
        if (active.sessionId != sessionId) return false
        val g = active.groundEvidence ?: return true
        return g.sessionId == sessionId
    }

    fun evidenceBelongsToSession(): Boolean = evidenceBelongsToSession(tracker.active())

    private fun emit(event: String, detail: String) = emitRaw("$event $detail")

    private fun emitRaw(line: String) {
        events?.invoke("session=${sessionId ?: "-"} $line")
    }

    fun setInRange(value: Boolean, nowMs: Long): Result {
        inRange = value
        if (!value) {
            lock.snapshot()   // 仅取快照，不改锁；离得远时不累积身份证据
        }
        return evaluate(nowMs, emptyList())
    }

    fun setBlockingConflict(value: Boolean) {
        if (blockingConflict == value) return
        blockingConflict = value
        emit("BLOCKING_CONFLICT", "value=$value")
    }

    /** 一次 OCR 命中。 */
    fun onTextHit(hit: TextHit, nowMs: Long): Result {
        val before = lock.snapshot()
        lock.observe(hit, nowMs)
        val after = lock.snapshot()
        if (before.hitCount == 0 && after.hitCount > before.hitCount) {
            emit("TARGET_SEEN", "hits=${after.hitCount} frames=${after.distinctFrames} " +
                "kind=${after.lastKind} alias=${after.matchedAlias.ifEmpty { "-" }}")
        }
        if (!before.everLocked && after.everLocked) {
            emit("TARGET_LOCKED", "hits=${after.hitCount} frames=${after.distinctFrames} " +
                "score=${"%.2f".format(after.score)} alias=${after.matchedAlias} src=${after.lastAliasSource}")
        }
        // 能力 2：同一条文本还进地标账（支持性/互斥地标走另一套判据）
        landmarks.add(landmarkIndex.classify(hit))
        // 能力 3：地址证据。只累计明确形式，光数字永不产生冲突。
        if (!addressExpectation.empty) {
            AddressExtractor.extract(hit.text)?.let { tok ->
                if (addressExpectation.compare(tok) == AddressOutcome.CONFLICT) {
                    lastAddress = AddressOutcome.CONFLICT
                    emit("ADDRESS_CONFLICT", "text=${hit.text.take(12)} expect=${addressExpectation}")
                } else if (addressExpectation.compare(tok) == AddressOutcome.MATCH &&
                    lastAddress != AddressOutcome.CONFLICT) {
                    lastAddress = AddressOutcome.MATCH
                }
            }
        }
        return evaluate(nowMs, listOf(hit.text))
    }

    /**
     * 一次入口候选。`source` 说清它是模型给的还是人工按出来的：
     * 人工框一律 `DEBUG_MANUAL_PROPOSAL`，不允许冒充检测结果。
     */
    fun onDoorDetected(left: Int, top: Int, right: Int, bottom: Int,
                       frameId: Long, tsMs: Long, nowMs: Long,
                       source: CandidateSource = CandidateSource.UNKNOWN,
                       debugPosition: DebugDoorPosition? = null): Result {
        tracker.prune(nowMs)
        tracker.observeDoor(left, top, right, bottom, frameId, tsMs, nowMs, source, debugPosition, sessionId)
        return evaluate(nowMs, emptyList())
    }

    /**
     * 调试面板的三个分区（能力：人工入口候选）。
     * 框由**当前帧真实尺寸**换算，与任何 View 的尺寸无关。
     */
    fun onDebugDoor(position: DebugDoorPosition, frameWidth: Int, frameHeight: Int,
                    frameId: Long, tsMs: Long, nowMs: Long): Result {
        val box = DebugDoorProposal.box(position, frameWidth, frameHeight)
            ?: return evaluate(nowMs, emptyList())
        return onDoorDetected(box[0], box[1], box[2], box[3], frameId, tsMs, nowMs,
            CandidateSource.DEBUG_MANUAL_PROPOSAL, position)
    }

    // ---------------------------------------------------------------- 统一视觉结果（今晚新接的入口）
    @Volatile var lastVision: Last10mVisionResult? = null
        private set
    /** 最近一份视觉结果的文字，已换算成像素坐标（与候选门同一空间）。 */
    @Volatile private var visionPixelTexts: List<TextHit> = emptyList()
    private val visionIds = LinkedHashMap<String, String>() // 视觉源给的门 id → 内部 doorId（按报告顺序）
    private val doorPositions = HashMap<String, DebugDoorPosition>()

    fun doorType(): DoorType = lastVision?.doorType ?: DoorType.UNKNOWN
    fun buttonLocation(): ButtonLocation = lastVision?.buttonLocation ?: ButtonLocation.UNKNOWN
    fun sceneSummary(): String? = lastVision?.sceneSummary
    fun visionProvenance(): Map<String, String> = lastVision?.provenance ?: emptyMap()

    /**
     * 吃一份统一视觉结果：文字进身份、门框进候选、地面进证据、门类型/按钮/摘要进展示层。
     *
     * 这里**不做关系判定**（那是 [TargetDoorRelationEngine] 的活），
     * 也不做到达判定。它只负责"把这帧看到的东西按来源登记好"。
     */
    fun onVisionResult(result: Last10mVisionResult, frameWidth: Int, frameHeight: Int,
                       nowMs: Long): VisionApply {
        if (!result.parseOk) {
            emit("VISION_REJECTED", "src=${result.source.label()} reasons=${result.violations} " +
                "frame=${result.frameId} —— 这一份结果一个字段都不用")
            evaluate(nowMs, emptyList())
            return VisionApply(0, 0, 0, false, emptyList())
        }
        lastVision = result
        // 视觉源的文字框是千分比（0–1000），候选门是像素。两套坐标直接比，
        // "招牌在这扇门上方"永远算不出重叠——所以在这里一次性换算成像素，
        // 之后 TargetLock、关系引擎、面板用的都是同一个坐标空间。
        val pixelTexts = result.texts.map { permilleHitToPixels(it, frameWidth, frameHeight) }
        visionPixelTexts = pixelTexts
        if (frameWidth <= 0 || frameHeight <= 0) {
            emit("VISION_TEXT_NO_FRAME_SIZE", "texts=${pixelTexts.size} frame=${frameWidth}x${frameHeight}" +
                " —— 文字框没法换算成像素，关系判定这次只能靠视觉自己的 relations")
        }
        var texts = 0
        for (t in pixelTexts) {
            onTextHit(t.copy(timestampMs = t.timestampMs.takeIf { ts -> ts > 0 } ?: nowMs), nowMs)
            texts++
        }
        val newDoors = ArrayList<String>()
        if (result.doors.isNotEmpty()) visionIds.clear()
        for (d in result.doors) {
            val box = d.boxInFrame(frameWidth, frameHeight)
            if (box == null) { emit("DOOR_REJECTED", "visionId=${d.id} reason=BOX_OR_FRAME_SIZE"); continue }
            val before = tracker.candidates().size
            val frameId = if (result.frameId > 0) result.frameId else frameIdsForVision(nowMs)
            // 新鲜度按**学到的时刻**算：云端一次请求要 1.5–2.5s，
            // 拿那一帧的拍摄时间去判"证据多旧"会让每个答案刚到手就已经过期。
            // 但它是哪一帧看到的必须留在事件里，迟到过久的答案由会话层丢弃。
            val id = tracker.observeDoor(box[0], box[1], box[2], box[3], frameId,
                nowMs, nowMs, candidateSourceOf(result.source), null, sessionId,
                EntranceTracker.DoorCadence.SPARSE_REPORT)
            visionIds[d.id] = id
            if (tracker.candidates().size > before) newDoors.add(id)
        }
        if (result.source != EvidenceSource.DEBUG_MANUAL &&
            result.source != EvidenceSource.MANUAL && result.doors.isNotEmpty()) {
            // 这一帧报告过的门之外，旧的检测候选一律退出：不在眼前就不能继续参与判定
            tracker.expireNotReported(visionIds.values.toSet(), result.frameId, nowMs)
        }
        var groundAttached = false
        if (result.ground.visible == true) {
            val active = tracker.active()
            if (active != null) {
                tracker.attachGround(active.doorId, GroundEvidence(
                    visible = true, step = result.ground.step == true,
                    curb = result.ground.curb == true, obstruction = result.ground.obstruction == true,
                    sourceFrameId = result.frameId,
                    timestampMs = nowMs,
                    note = "vision:${result.source.label()} frameTs=${result.timestampMs}",
                    source = groundSourceOf(result.source), sessionId))
                groundAttached = true
            }
        }
        emit("VISION_APPLIED", "src=${result.source.label()} texts=$texts doors=${result.doors.size} " +
            "new=${newDoors.size} relations=${result.relations.size} ground=$groundAttached " +
            "type=${result.doorType} button=${result.buttonLocation} latency=${result.latencyMs}ms " +
            "evidenceAge=${nowMs - result.timestampMs}ms")
        evaluate(nowMs, pixelTexts.map { it.text })
        return VisionApply(texts, result.doors.size, newDoors.size, groundAttached, newDoors)
    }

    /**
     * 视觉结果里的文字，已换算成**像素**坐标。
     * 关系引擎拿它和候选门比重叠，必须是同一个坐标空间（本地 OCR 一直是像素）。
     */
    fun visionTextHits(): List<TextHit> = visionPixelTexts

    /** 千分比文字框 → 像素。帧尺寸未知时原样返回，不能凭空造坐标。 */
    private fun permilleHitToPixels(hit: TextHit, width: Int, height: Int): TextHit {
        if (width <= 0 || height <= 0) return hit
        return hit.copy(
            left = hit.left * width / 1000, right = hit.right * width / 1000,
            top = hit.top * height / 1000, bottom = hit.bottom * height / 1000)
    }

    /** 关系结论落回候选门。UNKNOWN 什么都不做——"没判出来"不是一种判定结果。 */
    fun applyRelationDecisions(decisions: List<TargetDoorRelationEngine.Decision>,
                              source: EvidenceSource, nowMs: Long) {
        for (d in decisions) {
            when (d.verdict) {
                TargetDoorRelationEngine.Verdict.SAME_TARGET ->
                    tracker.confirmRelation(d.doorId, source, true, nowMs)
                TargetDoorRelationEngine.Verdict.NEIGHBOR ->
                    tracker.confirmRelation(d.doorId, source, false, nowMs)
                TargetDoorRelationEngine.Verdict.UNKNOWN -> {
                    val c = tracker.candidates().firstOrNull { it.doorId == d.doorId }
                    if (c != null && c.relationConfirmed != null) {
                        // 之前判过、现在依据不足：退回"未判"，而不是留一个旧的 true/false
                        c.relationConfirmed = null
                        c.relationSource = null
                        c.relationEvidenceMs = 0L
                    }
                    emit("RELATION_UNKNOWN", "door=${d.doorId} why=${d.whyUnknown ?: "依据不足"} " +
                        "evidence=${d.evidence.map { e -> e.kind }.joinToString("/")}")
                }
            }
        }
        // 判定完之后，注意力要跟着结论走：
        // 有一扇被判属目标 ⇒ 它就是活跃门；门前地面证据如果之前挂在别的门上，一起跟过来。
        val confirmed = tracker.candidates().firstOrNull { it.relationConfirmed == true }
        if (confirmed != null && tracker.active()?.doorId != confirmed.doorId) {
            tracker.setActive(confirmed.doorId, "relation_confirmed")
            val g = lastVision?.ground
            if (confirmed.groundEvidence == null && g?.visible == true) {
                tracker.attachGround(confirmed.doorId, GroundEvidence(
                    visible = true, step = g.step == true, curb = g.curb == true,
                    obstruction = g.obstruction == true,
                    sourceFrameId = lastVision?.frameId ?: -1L,
                    timestampMs = lastVision?.timestampMs ?: nowMs,
                    note = "随确认门迁移（原挂在别的候选上）",
                    source = lastVision?.let { groundSourceOf(it.source) } ?: GroundSource.UNKNOWN, sessionId))
            }
        }
        evaluate(nowMs, emptyList())
    }

    private fun groundSourceOf(s: EvidenceSource): GroundSource = when (s) {
        EvidenceSource.VLM, EvidenceSource.REMOTE_VLM -> GroundSource.VLM
        EvidenceSource.REPLAY -> GroundSource.REPLAY
        EvidenceSource.MANUAL, EvidenceSource.DEBUG_MANUAL -> GroundSource.DEBUG_MANUAL
        else -> GroundSource.GROUND_MODEL
    }

    private fun candidateSourceOf(s: EvidenceSource): CandidateSource = when (s) {
        EvidenceSource.VLM, EvidenceSource.REMOTE_VLM -> CandidateSource.VLM
        EvidenceSource.REPLAY -> CandidateSource.REPLAY
        EvidenceSource.MANUAL, EvidenceSource.DEBUG_MANUAL -> CandidateSource.DEBUG_MANUAL_PROPOSAL
        else -> CandidateSource.MODEL
    }

    private fun frameIdsForVision(nowMs: Long): Long = nowMs / 33

    data class VisionApply(
        val texts: Int, val doors: Int, val newDoors: Int,
        val groundAttached: Boolean, val newDoorIds: List<String>
    )

    /**
     * 内部 doorId → 视觉源给的门 id。
     *
     * 方向必须是这样：关系引擎拿候选门去查"视觉源管它叫什么"，
     * 查不到就等于这一扇没有任何视觉侧的关系结论。
     */
    fun visionDoorMapping(): Map<String, String> =
        visionIds.entries.associate { (visionId, doorId) -> doorId to visionId }

    /** VLM 的结构化事实。越界字段已在 [VlmFacts.fromFields] 里丢弃。 */
    fun onVlmFacts(facts: VlmFacts, nowMs: Long): Result {
        lastFacts = facts
        if (facts.violations.isNotEmpty()) {
            emit("VLM_VIOLATION", "fields=${facts.violations.joinToString("/")}")
        }
        facts.targetIdentity?.let {
            lock.observeVlmIdentity(it, facts.sourceFrameId, facts.timestampMs, nowMs)
        }
        val active = tracker.active()
        when (facts.entranceRelation) {
            RelationOutcome.LIKELY_SAME -> active?.let { tracker.confirmRelation(it.doorId, EvidenceSource.VLM, true) }
            RelationOutcome.CONFLICT -> active?.let { tracker.confirmRelation(it.doorId, EvidenceSource.VLM, false) }
            RelationOutcome.UNKNOWN -> { /* 不下结论，保持 VERIFY_RELATION */ }
        }
        if (facts.groundVisible == true && active != null) {
            // VLM 说"地面看得见"不等于"门前安全"：台阶/路缘沿用已有人工标记，
            // 来源必须标 VLM，这样面板与采集数据能分清是谁说的。
            tracker.attachGround(active.doorId,
                GroundEvidence(true, active.groundEvidence?.step ?: false,
                    active.groundEvidence?.curb ?: false, false,
                    facts.sourceFrameId, facts.timestampMs, "vlm_ground_visible",
                    GroundSource.VLM, sessionId))
        }
        return evaluate(nowMs, emptyList())
    }

    fun onGroundEvidence(ground: GroundEvidence, doorId: String?, nowMs: Long): Result {
        val id = doorId ?: tracker.active()?.doorId ?: return evaluate(nowMs, emptyList())
        tracker.attachGround(id, ground)
        return evaluate(nowMs, emptyList())
    }

    /** 目标锁失效时必须由上层调用，确保旧关系不会跟着新门。 */
    fun onTargetLost(reason: String, nowMs: Long): Result {
        tracker.invalidateRelations(reason)
        return evaluate(nowMs, emptyList())
    }

    fun setRouteBearing(bearingDegrees: Double?) {
        if (routeBearing == bearingDegrees) return
        routeBearing = bearingDegrees
        emit("ROUTE_BEARING", if (bearingDegrees == null) "cleared" else "set=%.0f".format(bearingDegrees))
    }

    /** 相机朝向由 Android 侧注入（PhoneDirection 的拍摄瞬间朝向）。 */
    fun setCameraHeading(headingDegrees: Double?, upright: Boolean) {
        cameraHeading = headingDegrees
        cameraUpright = upright
    }

    @Volatile private var routeBearing: Double? = null
    @Volatile private var cameraHeading: Double? = null
    @Volatile private var cameraUpright: Boolean = false

    fun evaluate(nowMs: Long, recentTexts: List<String>): Result {
        lastNowMs = nowMs
        val snapshot = lock.snapshotAt(nowMs)     // 读取即做过期检查
        if (snapshot.everLocked && snapshot.status != TargetLock.Status.TARGET_LOCKED &&
            tracker.candidates().any { it.relationConfirmed != null }) {
            tracker.invalidateRelations("target_lock_expired")
            emit("TARGET_EXPIRED", "relations_invalidated reason=target_lock_expired hits=${snapshot.hitCount}")
        }
        val active = tracker.active()
        val neighbor = tracker.neighborConflict(nowMs)
        // "多门"要数的是**这一帧还看得见的门**，不是历史上攒下来的所有候选
        val freshDoors = tracker.freshCount(nowMs)
        val relationConflict = tracker.relationConflict(tracker.active()?.doorId)
        val targetAge = if (snapshot.lastSeenMs > 0) ageMs(nowMs, snapshot.lastSeenMs) else Long.MAX_VALUE
        val sameSession = evidenceBelongsToSession(active)

        val decision = authority.decide(ArrivalAuthority.Input(
            nowMs = nowMs,
            targetLocked = snapshot.status == TargetLock.Status.TARGET_LOCKED,
            targetEverLocked = snapshot.everLocked,
            targetAgeMs = targetAge,
            activeDoor = tracker.active(),
            neighborConflict = neighbor,
            relationConflict = relationConflict,
            facts = lastFacts,
            blockingConflict = blockingConflict,
            sessionId = sessionId
        ))

        if (!inRange) {
            previousState = Last10mState.APPROACH
            previousDecision = ArrivalDecision.KEEP_SEARCHING
            previousNeedView = NeedView.NONE
            lastScan = ScanDirection.UNKNOWN
            lastScanReason = if (routeBearing == null) ScanUnknownReason.ROUTE_BEARING_MISSING
                else if (cameraHeading == null) ScanUnknownReason.HEADING_MISSING
                else ScanUnknownReason.NONE
            return Result(previousState, ArrivalDecision.KEEP_SEARCHING, "",
                ScanDirection.UNKNOWN, false, 0, null, 0, false, null,
                lastFacts?.violations ?: emptyList(),
                needView = NeedView.NONE, reasonCodes = listOf(ReasonCode.NONE),
                scanReason = lastScanReason)
        }

        val scan = scanCoach.update(routeBearing, cameraHeading, nowMs, cameraUpright)
        lastScan = scan
        lastScanReason = if (scan != ScanDirection.UNKNOWN) ScanUnknownReason.NONE
            else when {
                routeBearing == null -> ScanUnknownReason.ROUTE_BEARING_MISSING
                cameraHeading == null -> ScanUnknownReason.HEADING_MISSING
                !cameraUpright -> ScanUnknownReason.PHONE_NOT_UPRIGHT
                !routeBearing!!.isFinite() || !cameraHeading!!.isFinite() -> ScanUnknownReason.SENSOR_VALUE_INVALID
                else -> ScanUnknownReason.HEADING_MISSING
            }

        val state = when {
            decision.outcome == ArrivalDecision.ARRIVAL_CONFIRMED -> Last10mState.ARRIVAL_CONFIRMED
            decision.outcome == ArrivalDecision.RELATION_CONFLICT ||
                decision.outcome == ArrivalDecision.REOBSERVE_TARGET ||
                decision.outcome == ArrivalDecision.REOBSERVE_DOOR ||
                decision.outcome == ArrivalDecision.TARGET_LOST -> Last10mState.REOBSERVE
            snapshot.status != TargetLock.Status.TARGET_LOCKED -> Last10mState.SEARCH_TARGET
            active == null -> if (tracker.candidates().isEmpty())
                Last10mState.TARGET_LOCKED else Last10mState.SEARCH_ENTRANCE
            active.relationConfirmed == null ->
                if (lastFacts == null) Last10mState.ENTRANCE_CANDIDATE else Last10mState.VERIFY_RELATION
            active.relationConfirmed == false -> Last10mState.VERIFY_RELATION
            !active.groundObserved -> Last10mState.VERIFY_GROUND
            else -> Last10mState.VERIFY_GROUND
        }
        val stateChanged = state != previousState
        previousState = state
        val nextView = when (state) {
            Last10mState.SEARCH_TARGET -> if (scan == ScanDirection.UNKNOWN) null else "sign"
            Last10mState.SEARCH_ENTRANCE, Last10mState.ENTRANCE_CANDIDATE -> "door"
            Last10mState.VERIFY_RELATION -> "storefront"
            Last10mState.VERIFY_GROUND -> "ground"
            Last10mState.REOBSERVE -> lastFacts?.needView ?: "wider"
            else -> null
        }
        val ground = active?.groundEvidence
        val plan = NeedViewPlanner.plan(EvidenceSnapshot(
            targetLocked = snapshot.status == TargetLock.Status.TARGET_LOCKED,
            targetEverLocked = snapshot.everLocked,
            targetAgeMs = targetAge,
            doorCandidates = freshDoors,
            hasActiveDoor = active != null,
            relationConfirmed = active?.relationConfirmed,
            ground = groundStateOf(ground),
            neighborConflict = neighbor,
            relationConflict = relationConflict,
            addressConflict = lastAddress == AddressOutcome.CONFLICT,
            landmarkConflict = landmarks.conflict(nowMs),
            scan = scan,
            groundAgeMs = ground?.let { ageMs(nowMs, it.timestampMs) } ?: 0L,
            scanReason = lastScanReason,
            groundSource = ground?.source ?: GroundSource.UNKNOWN,
            sameSession = sameSession
        ))
        if (stateChanged) {
            emit("STATE_TRANSITION", "to=${state.name} decision=${decision.outcome.name} " +
                "frame=${active?.sourceFrameId ?: -1L} doors=${tracker.candidates().size}")
        }
        if (decision.outcome != previousDecision) {
            emit("DECISION", "to=${decision.outcome.name} reason=${decision.reason} " +
                "missing=${decision.missing.joinToString("/")}")
            previousDecision = decision.outcome
        }
        if (plan.view != previousNeedView) {
            emit("NEED_VIEW", "to=${plan.view.name} reasons=${plan.reasons.joinToString("/")}")
            previousNeedView = plan.view
        }
        return Result(
            state = state,
            decision = decision.outcome,
            prompt = Last10mPrompts.forDecision(decision.outcome, state),
            scanDirection = scan,
            targetLocked = snapshot.status == TargetLock.Status.TARGET_LOCKED,
            targetHits = snapshot.hitCount,
            activeDoorId = active?.doorId,
            candidateCount = freshDoors,
            groundObserved = active?.groundObserved ?: false,
            nextView = nextView,
            violations = lastFacts?.violations ?: emptyList(),
            needView = plan.view,
            reasonCodes = plan.reasons,
            viewPrompt = plan.prompt,
            scanReason = lastScanReason,
            groundState = groundStateOf(ground),
            groundSource = ground?.source ?: GroundSource.UNKNOWN
        )
    }
}

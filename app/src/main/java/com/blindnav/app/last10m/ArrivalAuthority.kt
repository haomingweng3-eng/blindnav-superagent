package com.blindnav.app.last10m

/**
 * 到达授权（任务书第十一节）。
 *
 * 六个条件**全部**成立才允许 ARRIVAL_CONFIRMED：
 *  1. 目标身份已确认（多帧或双独立来源，见 TargetLock）
 *  2. 存在入口候选
 *  3. 目标—入口关系已确认
 *  4. 证据新鲜（不是几秒前拼出来的旧画面）
 *  5. 门前地面已观察
 *  6. 无邻门冲突 / 无阻挡冲突
 *
 * 缺任何一条，只能落到六个非到达输出上。**没有**"自由移动多少米"这种输出。
 * 判序刻意从"最不可原谅的错误"往回退：先关系冲突、再目标丢失、再地面，
 * 因为把隔壁门说成目标入口比"多让用户扫一次"严重得多。
 */
class ArrivalAuthority(
    private val maxEvidenceAgeMs: Long = 2000L,
    private val maxRelationAgeMs: Long = 4000L
) {

    data class Input(
        val nowMs: Long,
        val targetLocked: Boolean,
        /** 从没锁过 = 还在找招牌；锁过又丢 = 目标丢失。两者的提示与状态不同，不能合并。 */
        val targetEverLocked: Boolean,
        val targetAgeMs: Long,
        val activeDoor: EntranceCandidate?,
        val neighborConflict: Boolean,
        val relationConflict: Boolean,
        val facts: VlmFacts?,
        val blockingConflict: Boolean,
        /**
         * 当前会话身份。候选门与地面证据必须来自同一个会话才允许到达。
         * 这一条以前只靠"每个会话一台新机器"的约定，现在写进裁决里。
         */
        val sessionId: String? = null
    )

    data class Decision(
        val outcome: ArrivalDecision,
        val reason: String,
        val missing: List<String>
    )

    /**
     * 到达确认的**全部**前置条件，独立于下面的分支逻辑写一遍。
     * fuzz 测试同时断言两个方向：
     *  - ARRIVAL_CONFIRMED ⇒ 每一条都成立；
     *  - 每一条都成立 ⇒ ARRIVAL_CONFIRMED。
     * 两个实现哪天走偏了，这条测试就会红——这才是"确定性到达授权"的可验证形式。
     */
    /**
     * 关系证据是否还算数。
     * VLM / 云端 / 回放这些都是**临时**结论：几秒前那次判断不能给现在这一帧背书
     * （人已经往前走了，看到的可能是隔壁那一扇）。
     * 人工声明是标签不是感知，所以不查新鲜度；本地模型来源同样不查（它给的是当帧框）。
     */
    private fun relationIsFresh(input: Input, door: EntranceCandidate): Boolean {
        val provisional = door.relationSource == EvidenceSource.VLM ||
            door.relationSource == EvidenceSource.REMOTE_VLM ||
            door.relationSource == EvidenceSource.REPLAY
        if (!provisional) return true
        if (input.facts != null) return input.facts.fresh(input.nowMs, maxRelationAgeMs)
        if (door.relationEvidenceMs > 0L) return ageMs(input.nowMs, door.relationEvidenceMs) <= maxRelationAgeMs
        // 临时来源但连"什么时候判的"都没有 —— 当作不新鲜，宁可让用户重看一次
        return false
    }

    fun guards(input: Input): List<Pair<String, Boolean>> {
        val door = input.activeDoor
        val ground = door?.groundEvidence
        return listOf(
            "targetConfirmed" to input.targetLocked,
            "targetFresh" to (input.targetAgeMs <= maxEvidenceAgeMs),
            "doorCandidate" to (door != null),
            "doorFresh" to (door != null && door.ageMs(input.nowMs) <= maxEvidenceAgeMs),
            "relationConfirmed" to (door?.relationConfirmed == true),
            "relationFresh" to (door != null && door.relationConfirmed == true && relationIsFresh(input, door)),
            "groundObserved" to (ground != null && ground.visible && !ground.obstruction),
            "groundFresh" to (ground != null &&
                ageMs(input.nowMs, ground.timestampMs) <= maxEvidenceAgeMs),
            "noConflict" to (!input.relationConflict && !input.blockingConflict &&
                (!input.neighborConflict || door?.relationConfirmed == true)),
            "sameSession" to (door != null && door.sessionId == input.sessionId &&
                (ground == null || ground.sessionId == input.sessionId)),
            "timestampValid" to (door != null && door.lastSeenMs <= input.nowMs &&
                (ground == null || ground.timestampMs <= input.nowMs) &&
                input.targetAgeMs != Long.MAX_VALUE)
        )
    }

    /** 到达确认的短路检查：任何一条不成立就不是到达。 */
    fun allGuardsHold(input: Input): Boolean = guards(input).all { it.second }

    fun decide(input: Input): Decision {
        val missing = ArrayList<String>()
        if (!input.targetLocked) missing.add("target_identity")
        if (input.activeDoor == null) missing.add("entrance_candidate")
        if (input.activeDoor?.relationConfirmed != true) missing.add("relation_confirmed")
        if (input.activeDoor?.groundObserved != true) missing.add("ground_observed")

        // 跨会话证据：先于一切。别的会话攒出来的门和地面，在本会话里等于不存在。
        val foreign = input.activeDoor != null && input.activeDoor.sessionId != input.sessionId
        val foreignGround = input.activeDoor?.groundEvidence != null &&
            input.activeDoor.groundEvidence!!.sessionId != input.sessionId
        if (foreign || foreignGround) {
            return Decision(ArrivalDecision.REOBSERVE_TARGET,
                "证据来自另一个会话（door=${input.activeDoor?.sessionId} " +
                    "ground=${input.activeDoor?.groundEvidence?.sessionId}），已作废", missing)
        }

        // 关系冲突优先于一切：宁可让用户重扫，也不能确认一个可能属于隔壁店的门。
        if (input.relationConflict) {
            return Decision(ArrivalDecision.RELATION_CONFLICT,
                "已确认关系的门与当前活跃候选不是同一扇", missing)
        }
        if (input.neighborConflict && input.activeDoor?.relationConfirmed != true) {
            return Decision(ArrivalDecision.REOBSERVE_DOOR,
                "同一视野内有多个候选门，尚未确认哪一个属于目标", missing)
        }
        if (!input.targetLocked) {
            if (!input.targetEverLocked) {
                // 还没确认过目标身份：正常继续找招牌，不能报"目标丢失"吓用户，
                // 也不能因为画面里有门就跳到重扫。
                return Decision(ArrivalDecision.KEEP_SEARCHING, "尚未确认目标身份", missing)
            }
            // 曾经锁过又丢了：根因是身份，不是门。报 TARGET_LOST 让用户重扫招牌，
            // 而不是 REOBSERVE_DOOR 把注意力引向门。
            return Decision(ArrivalDecision.TARGET_LOST, "目标身份确认已失效，需重新看到招牌", missing)
        }
        if (input.targetAgeMs > maxEvidenceAgeMs) {
            return Decision(ArrivalDecision.REOBSERVE_TARGET,
                "目标证据已过期 ${input.targetAgeMs}ms", missing)
        }
        val door = input.activeDoor ?: return Decision(ArrivalDecision.KEEP_SEARCHING,
            "尚未出现入口候选", missing)
        if (door.ageMs(input.nowMs) > maxEvidenceAgeMs) {
            return Decision(ArrivalDecision.REOBSERVE_DOOR, "入口候选证据过期", missing)
        }
        if (door.relationConfirmed == false) {
            // 明确判过"不属于同一店面"，比没判过更该重扫，而不是继续找。
            return Decision(ArrivalDecision.RELATION_CONFLICT,
                "已判定该候选门不属于目标店面", missing)
        }
        if (door.relationConfirmed != true) {
            return Decision(ArrivalDecision.KEEP_SEARCHING, "招牌与门的关系尚未确认", missing)
        }
        if (door.relationSource == EvidenceSource.VLM || door.relationSource == EvidenceSource.REMOTE_VLM ||
            door.relationSource == EvidenceSource.REPLAY) {
            if (!relationIsFresh(input, door)) return Decision(ArrivalDecision.REOBSERVE_DOOR,
                "关系判定只来自临时来源（${door.relationSource?.label()}）且其证据已过期", missing)
        }
        val ground = door.groundEvidence
        if (ground == null || !ground.visible) {
            return Decision(ArrivalDecision.LOOK_DOWN_GROUND,
                "门前区域未进入画面，UNKNOWN 不等于 SAFE", missing)
        }
        if (ground.obstruction) {
            return Decision(ArrivalDecision.LOOK_DOWN_GROUND, "门前有明显阻挡，需换视角", missing)
        }
        // 地面证据也必须新鲜。只查"有没有地面证据"是不够的：
        // 30 秒前低头看到过一次安全地面，不能给现在这一帧的门背书——
        // 人已经走开、门前可能多了台阶或障碍。fuzz 抓到过这个反例。
        val groundAge = ageMs(input.nowMs, ground.timestampMs)
        if (groundAge > maxEvidenceAgeMs) {
            return Decision(ArrivalDecision.LOOK_DOWN_GROUND,
                "门前地面证据已过期 ${groundAge}ms，需重新确认地面", missing)
        }
        if (input.blockingConflict) {
            return Decision(ArrivalDecision.LOOK_DOWN_GROUND, "障碍证据与到达冲突", missing)
        }
        // 兜底：分支逻辑说"可以到达"，但独立写的那份条件表里有任何一条不成立，
        // 就说明两处实现走偏了。宁可退回到重扫，也不带着分歧宣布到达。
        val unmet = guards(input).filter { !it.second }.map { it.first }
        if (unmet.isNotEmpty()) {
            return Decision(ArrivalDecision.REOBSERVE_DOOR,
                "到达前置条件未全部成立：${unmet.joinToString("/")}", missing)
        }
        // 台阶/路缘不阻止确认到达，但必须留在提示里——它们正是最后一步要小心的东西。
        // 人工声明的地面（DEBUG_MANUAL）只能说"门前区域已记录"，不能说"门前安全"。
        val groundNote = when (ground.source) {
            GroundSource.DEBUG_MANUAL -> "，门前区域按人工声明已记录（非视觉确认）"
            GroundSource.VLM -> "，门前可见性来自 VLM（临时结论）"
            GroundSource.REPLAY -> "，门前可见性来自离线回放（临时结论）"
            GroundSource.GROUND_MODEL -> "，门前可见性来自地面模型"
            GroundSource.UNKNOWN -> ""
        }
        return Decision(ArrivalDecision.ARRIVAL_CONFIRMED,
            "六条件齐备" + listOfNotNull(
                if (ground.step) "门前有台阶" else null,
                if (ground.curb) "门前有路缘" else null
            ).joinToString("、").let { if (it.isEmpty()) "" else "（$it）" } + groundNote,
            emptyList())
    }
}

enum class ArrivalDecision {
    ARRIVAL_CONFIRMED,
    KEEP_SEARCHING,
    REOBSERVE_TARGET,
    REOBSERVE_DOOR,
    LOOK_DOWN_GROUND,
    TARGET_LOST,
    RELATION_CONFLICT
}

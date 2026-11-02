package com.blindnav.app.last10m

/**
 * 扫描方向提示（任务书第五节）。
 *
 * 只回答"目标大概在左边、正前还是右边，请朝那边扫"，
 * **绝不**输出"往左走"。罗盘本身有金属干扰与手机姿态问题
 * （现有 PhoneDirection 只在 accuracy≥2 且与曝光时刻 ±150ms 内才给值），
 * 所以任何不确定都返回 UNKNOWN，并且不产生提示。
 */
object ScanDirectionResolver {

    /** 正前判定半宽（度）。手持朝向误差远大于此，宁窄勿宽。 */
    const val FRONT_HALF_WIDTH_DEG = 30.0

    fun resolve(
        routeBearingDegrees: Double?,
        cameraHeadingDegrees: Double?,
        upright: Boolean = true
    ): ScanDirection {
        val target = routeBearingDegrees ?: return ScanDirection.UNKNOWN
        val heading = cameraHeadingDegrees ?: return ScanDirection.UNKNOWN
        if (!upright) return ScanDirection.UNKNOWN
        if (!target.isFinite() || !heading.isFinite()) return ScanDirection.UNKNOWN
        val relative = ((target - heading + 540.0) % 360.0) - 180.0   // 正=目标在右侧
        return when {
            absolute(relative) <= FRONT_HALF_WIDTH_DEG -> ScanDirection.FRONT
            relative > 0 -> ScanDirection.RIGHT
            else -> ScanDirection.LEFT
        }
    }

    /** 目标与当前朝向的夹角，用于判断"要不要继续转"。 */
    fun offsetDegrees(routeBearingDegrees: Double?, cameraHeadingDegrees: Double?): Double? {
        val target = routeBearingDegrees ?: return null
        val heading = cameraHeadingDegrees ?: return null
        return ((target - heading + 540.0) % 360.0) - 180.0
    }

    private fun absolute(v: Double) = if (v < 0) -v else v
}

/**
 * 提示语（任务书第十二节）。短句，不描述环境，不承诺安全。
 */
object Last10mPrompts {

    fun forScan(direction: ScanDirection): String? = when (direction) {
        ScanDirection.LEFT -> "目标大概在左侧，请站稳后缓慢向左扫描"
        ScanDirection.RIGHT -> "目标大概在右侧，请站稳后缓慢向右扫描"
        ScanDirection.FRONT -> "目标在正前方，请缓慢左右扫一下"
        ScanDirection.UNKNOWN -> null
    }

    fun forDecision(decision: ArrivalDecision, state: Last10mState): String = when (decision) {
        ArrivalDecision.ARRIVAL_CONFIRMED -> "入口已确认"
        ArrivalDecision.KEEP_SEARCHING -> if (state == Last10mState.SEARCH_ENTRANCE ||
            state == Last10mState.TARGET_LOCKED) "看到目标招牌，正在找入口" else "继续找目标招牌"
        ArrivalDecision.REOBSERVE_TARGET -> "当前无法确认这是目标入口，请重新扫描"
        ArrivalDecision.REOBSERVE_DOOR -> "发现多个可能入口，请继续缓慢扫描"
        ArrivalDecision.LOOK_DOWN_GROUND -> "入口可能已找到，请稍微向下拍摄门前区域"
        ArrivalDecision.TARGET_LOST -> "目标招牌已离开画面，请重新扫描"
        ArrivalDecision.RELATION_CONFLICT -> "当前无法确认这是目标入口，请重新扫描"
    }

    /** 邻门专用：把"可能是隔壁"这件事说清楚，不要含糊地催用户走。 */
    const val NEIGHBOR_WARNING = "旁边还有别的门，先别进去"

    /**
     * 人工声明地面的回执（任务书第 12 节）。
     * 只能说"已记录"，不能说"门前安全"——长按面板只证明人看过，不证明没台阶。
     */
    const val GROUND_RECORDED = "门前区域已记录。"

    /**
     * 门类型（任务书第 10 节）。UNKNOWN 一律不说。
     * 措辞只讲"门是什么样"，不讲"能不能过"——后者是安全结论，不归这一层。
     */
    fun forDoorType(t: DoorType): String? = when (t) {
        DoorType.AUTOMATIC_SLIDING -> "那是自动移门，它会自己滑动。"
        DoorType.PUSH_PULL -> "那扇门可能要推开或拉开。"
        DoorType.REVOLVING -> "前面可能是旋转门。"
        DoorType.OPEN_DOORWAY -> "那里是一个敞开的门洞。"
        DoorType.UNKNOWN -> null
    }

    /** 开门按钮位置（任务书第 11 节）。同样：不知道就不说。 */
    fun forButton(b: ButtonLocation): String? = when (b) {
        ButtonLocation.LEFT_OF_DOOR -> "门左侧可能有开门按钮。"
        ButtonLocation.RIGHT_OF_DOOR -> "门右侧可能有开门按钮。"
        ButtonLocation.ON_DOOR -> "门上有开门按钮。"
        ButtonLocation.UNKNOWN -> null
    }

    /**
     * 门前地面风险提示。注意两点：
     *  - 说"可能"，因为云端 VLM 的判读是临时结论，不是验证过的安全事实；
     *  - 绝不说"安全/可以通过"，这类词在语音策略里是硬禁的。
     */
    fun forGroundHazard(step: Boolean, curb: Boolean, obstruction: Boolean): String? = when {
        obstruction -> "门前可能有东西挡着。"
        step -> "门前可能有一级台阶。"
        curb -> "门前可能有一道路缘。"
        else -> null
    }
}

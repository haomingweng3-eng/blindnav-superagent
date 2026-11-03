package com.blindnav.app.last10m

/**
 * VLM / 回放 JSON → [Last10mVisionResult] 的严格解析（任务书第 6 节）。
 *
 * 三条规矩：
 *  1. **解析失败就整体 UNKNOWN**。JSON 坏了、坐标越界、bbox 顺序反了——
 *     这份结果一个字段都不用，直接记 `VLM_UNPARSEABLE`。
 *     半份能用的结果最危险：它会让人以为"系统看到了门"，其实只看到了半句话。
 *  2. **不猜默认值**。字段缺失就是 null；
 *     `"step": "maybe"` 这类不严格的写法一律当 null（不是 false，也不是 true）。
 *  3. **越界措辞只丢那一格**。自由文本（scene_summary）里出现"安全/可以走/几米/到达"
 *     这类结论性说法时，丢掉这一个字段并记 violation，
 *     结构化字段仍然可用——这不是猜，而是明确拒收不合格的那一格。
 */
object VisionResultParser {

    /** 自由文本里的越权措辞。运动决策、安全结论、距离承诺一律不许从 VLM 流到用户耳朵里。 */
    private val FORBIDDEN_TEXT = Regex(
        "安全|可以通过|可以通行|放心|到达|到了|离你|[0-9]+\\s*[米步]|往[左右]走|向[左右]挪|往前走|向?前走|turn|walk|go\\s+(left|right)|meters?|safe",
        RegexOption.IGNORE_CASE)

    private val DOOR_TYPES = mapOf(
        "AUTOMATIC_SLIDING" to DoorType.AUTOMATIC_SLIDING,
        "SLIDING" to DoorType.AUTOMATIC_SLIDING,
        "AUTO" to DoorType.AUTOMATIC_SLIDING,
        "PUSH_PULL" to DoorType.PUSH_PULL,
        "PUSH" to DoorType.PUSH_PULL,
        "PULL" to DoorType.PUSH_PULL,
        "SWING" to DoorType.PUSH_PULL,
        "REVOLVING" to DoorType.REVOLVING,
        "OPEN_DOORWAY" to DoorType.OPEN_DOORWAY,
        "DOORWAY" to DoorType.OPEN_DOORWAY,
        "OPEN" to DoorType.OPEN_DOORWAY)

    private val BUTTONS = mapOf(
        "LEFT_OF_DOOR" to ButtonLocation.LEFT_OF_DOOR,
        "LEFT" to ButtonLocation.LEFT_OF_DOOR,
        "RIGHT_OF_DOOR" to ButtonLocation.RIGHT_OF_DOOR,
        "RIGHT" to ButtonLocation.RIGHT_OF_DOOR,
        "ON_DOOR" to ButtonLocation.ON_DOOR,
        "DOOR" to ButtonLocation.ON_DOOR)

    /**
     * @param rejectEmptyBoxes 门的空列表是否算"这源什么都没给"。
     *        对 VLM 来说空列表是有信息量的（画面里确实没有门），所以默认 false。
     */
    fun parse(rawJson: String?, frameId: Long, timestampMs: Long, source: EvidenceSource,
              latencyMs: Long = 0L): Last10mVisionResult {
        if (rawJson.isNullOrBlank()) {
            return Last10mVisionResult.unknown(frameId, timestampMs, source, "EMPTY_RESPONSE")
        }
        val obj = runCatching { MiniJson.parseObject(rawJson) }.getOrElse {
            return Last10mVisionResult.unknown(frameId, timestampMs, source,
                "UNPARSEABLE=${it.javaClass.simpleName}")
        }
        if (obj == null) {
            return Last10mVisionResult.unknown(frameId, timestampMs, source, "NOT_AN_OBJECT")
        }
        val violations = ArrayList<String>()

        // —— 文本
        val texts = ArrayList<TextHit>()
        for ((index, t) in obj.objList("ocr_texts").withIndex()) {
            val text = t.string("text") ?: continue
            val box = t.permilleBox("bbox")
            if (box == null) {
                violations.add("ocr_bbox_bad[$index]")
                continue
            }
            texts.add(TextHit(text = text, left = box[0], top = box[1], right = box[2], bottom = box[3],
                confidence = normalizeConfidence(t.doubleStrict("confidence")) ?: 0.0,
                sourceFrameId = frameId, timestampMs = timestampMs, source = source))
        }

        // —— 门候选
        val doors = ArrayList<VisionDoor>()
        for ((index, d) in obj.objList("doors").withIndex()) {
            val box = d.permilleBox("bbox")
            if (box == null) { violations.add("door_bbox_bad[${d.string("id") ?: index}]"); continue }
            doors.add(VisionDoor(id = d.string("id") ?: "door_${index + 1}",
                boxPermille = box, confidence = normalizeConfidence(d.doubleStrict("confidence"))))
        }

        // —— 关系
        val relations = ArrayList<VisionRelation>()
        for (r in obj.objList("relations")) {
            val id = r.string("door_id") ?: continue
            val conf = normalizeConfidence(r.doubleStrict("confidence"))
            val raw = r.string("relation")
            if (raw == null) { relations.add(VisionRelation(id, RelationOutcome.UNKNOWN, conf)); continue }
            val outcome = when (raw.uppercase().replace('-', '_')) {
                "SAME_TARGET", "SAME_STOREFRONT", "LIKELY_SAME", "SAME" -> RelationOutcome.LIKELY_SAME
                "NEIGHBOR", "NEIGHBOUR", "DIFFERENT_STOREFRONT", "NOT_SAME", "CONFLICT" -> RelationOutcome.CONFLICT
                else -> { violations.add("relation_word_unknown=$raw"); RelationOutcome.UNKNOWN }
            }
            relations.add(VisionRelation(id, outcome, conf))
        }

        // —— 地面
        val g = obj.obj("ground")
        val ground = if (g == null) VisionGround.UNKNOWN else VisionGround(
            visible = g.booleanStrict("visible"), step = g.booleanStrict("step"),
            curb = g.booleanStrict("curb"), obstruction = g.booleanStrict("obstruction"))

        // —— 门类型 / 按钮（词表外的写法一律 UNKNOWN，不硬归类）
        val doorType = DOOR_TYPES[obj.string("door_type")?.uppercase()?.replace('-', '_')] ?: DoorType.UNKNOWN
        val button = BUTTONS[obj.string("button_location")?.uppercase()?.replace('-', '_')] ?: ButtonLocation.UNKNOWN

        // —— 自由文本：先过措辞闸
        var summary = obj.string("scene_summary")
        val summaryHit = summary?.let { FORBIDDEN_TEXT.find(it) }
        if (summaryHit != null) {
            violations.add("forbidden_phrase_in_scene=${summaryHit.value}")
            summary = null
        }
        val identity = obj.string("target_identity")
        val identityBad = identity?.let { FORBIDDEN_TEXT.find(it) }
        if (identityBad != null) { violations.add("forbidden_phrase_in_identity=$identity"); }

        val provenance = buildMap {
            if (texts.isNotEmpty()) put("texts", source.label())
            if (doors.isNotEmpty()) put("doors", source.label())
            if (relations.isNotEmpty()) put("relations", source.provisionalLabel())
            if (ground != VisionGround.UNKNOWN) put("ground", source.provisionalLabel())
            if (doorType != DoorType.UNKNOWN) put("doorType", source.provisionalLabel())
            if (button != ButtonLocation.UNKNOWN) put("button", source.provisionalLabel())
            if (summary != null) put("scene", source.provisionalLabel())
        }

        return Last10mVisionResult(
            frameId = frameId, timestampMs = timestampMs, source = source,
            texts = texts, doors = doors,
            targetVisible = obj.booleanStrict("target_visible"),
            targetIdentity = if (identityBad == null) identity else null,
            targetConfidence = normalizeConfidence(obj.doubleStrict("target_confidence")),
            relations = relations, ground = ground,
            doorType = doorType, buttonLocation = button,
            sceneSummary = summary, needViewHint = obj.string("need_view"),
            provenance = provenance, parseOk = true,
            violations = violations, latencyMs = latencyMs)
    }

    /** 允许 0–1 或 0–1000 两种写法；范围外一律当"不知道"，不做截断。 */
    private fun normalizeConfidence(v: Double?): Double? {
        if (v == null || !v.isFinite()) return null
        return when {
            v in 0.0..1.0 -> v
            v in 1.0..1000.0 -> v / 1000.0
            else -> null
        }
    }
}

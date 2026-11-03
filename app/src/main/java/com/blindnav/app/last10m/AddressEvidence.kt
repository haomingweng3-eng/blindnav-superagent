package com.blindnav.app.last10m

/**
 * 门牌 / 楼号 / 入口编号证据（能力 3）。
 *
 * 便宜但很值钱：读到一个"161号"或"B Building"，能直接把"是不是这一扇"钉住；
 * 反过来目标 102 而门上是 101，必须产生冲突，**不能因为店名大致对就继续确认**。
 *
 * 但真实街景里的数字绝大多数不是门牌。从 LSVT 真实转写里抓的含数字串就是证据：
 *   5元 / 24小时 / 2018 / 361° / 2F / 3F / 1664 / 52863926 / 联系电话：13720158583 / 6X818 / JQ3 / 04
 * 所以这里的原则是**宁可漏，不可错判冲突**：
 *  - 只有带明确地址后缀的形式（161号 / 3号楼 / 3号入口 / 1单元 / Building B）才允许判 MATCH 或 CONFLICT；
 *  - 光秃秃的数字（"24"、"100"）最多算候选，只允许 MATCH 已存在的期望，**永远不产生 CONFLICT**；
 *  - 价格/时长/日期/楼层/电话一律归为噪声，不参与地址判断。
 */
enum class AddressKind {
    HOUSE_NUMBER, BUILDING_NUMBER, BUILDING_LABEL, ENTRY_NUMBER, UNIT, FLOOR,
    NUMBER_CANDIDATE, PRICE, HOURS, DATE, PHONE, DISTANCE, NOISE
}

data class AddressToken(val raw: String, val kind: AddressKind, val number: Int?, val letter: String?)

enum class AddressOutcome { MATCH, CONFLICT, UNKNOWN }

object AddressExtractor {

    // 注意不能写成 (元|...)：会把"1单元"当成价格。要求数字紧挨着货币字，或明确的小数/折扣形式。
    private val PRICE = Regex("(\\d\\s*(元|块|块钱)|[0-9.]+\\s*折|%|‰|￥|¥)")
    private val HOURS = Regex("(小时|hour|24h)", RegexOption.IGNORE_CASE)
    private val DATE = Regex("(19|20)\\d{2}\\s*(年|/|-)\\s*\\d*")
    private val PHONEISH = Regex("(电话|联系|手机|热线|tel|fax)", RegexOption.IGNORE_CASE)
    private val DISTANCE = Regex("(米|m|km|公里)$", RegexOption.IGNORE_CASE)
    private val FLOOR = Regex("^(\\d{1,2})\\s*[Ff楼层]$|^[Ff]\\s*(\\d{1,2})$")
    private val ENTRY = Regex("(\\d{1,3})\\s*号\\s*(入口|门|口|进出口|大门)")
    private val BUILDING_NUM = Regex("(\\d{1,3})\\s*号楼")
    private val UNIT = Regex("(\\d{1,3})\\s*单元")
    private val HOUSE = Regex("(\\d{1,4})\\s*号(?!\\s*[Ff楼层])")
    private val BUILDING_LABEL = Regex("([A-Z])\\s*(座|栋|幢|区)|building\\s*([A-Z])", RegexOption.IGNORE_CASE)
    private val BARE = Regex("^\\d{1,4}$")

    fun extract(text: String): AddressToken? {
        val t = text.trim()
        if (t.isEmpty() || !t.any { it.isDigit() || it in 'A'..'Z' || it in 'a'..'Z' }) return null
        val digits = Regex("(\\d+)").find(t)?.groupValues?.get(1)
        val num = digits?.toIntOrNull()

        // 先排噪声，顺序有意义：电话/价格比"号"更容易误吞
        if (PHONEISH.containsMatchIn(t) || (digits?.length ?: 0) >= 7)
            return AddressToken(t, AddressKind.PHONE, null, null)
        if (PRICE.containsMatchIn(t)) return AddressToken(t, AddressKind.PRICE, null, null)
        if (HOURS.containsMatchIn(t)) return AddressToken(t, AddressKind.HOURS, null, null)
        if (DATE.containsMatchIn(t)) return AddressToken(t, AddressKind.DATE, null, null)
        if (DISTANCE.containsMatchIn(t) && num != null && num <= 999)
            return AddressToken(t, AddressKind.DISTANCE, num, null)

        ENTRY.find(t)?.let { return AddressToken(t, AddressKind.ENTRY_NUMBER, it.groupValues[1].toIntOrNull(), null) }
        BUILDING_NUM.find(t)?.let { return AddressToken(t, AddressKind.BUILDING_NUMBER, it.groupValues[1].toIntOrNull(), null) }
        UNIT.find(t)?.let { return AddressToken(t, AddressKind.UNIT, it.groupValues[1].toIntOrNull(), null) }
        FLOOR.find(t)?.let { m ->
            val v = (m.groupValues[1].ifEmpty { m.groupValues[2] }).toIntOrNull()
            return AddressToken(t, AddressKind.FLOOR, v, null)
        }
        HOUSE.find(t)?.let { return AddressToken(t, AddressKind.HOUSE_NUMBER, it.groupValues[1].toIntOrNull(), null) }
        BUILDING_LABEL.find(t)?.let { m ->
            val letter = (m.groupValues[1].ifEmpty { m.groupValues[2] }).uppercase()
            return AddressToken(t, AddressKind.BUILDING_LABEL, null, letter.ifEmpty { null })
        }
        if (BARE.matches(t)) {
            // 光数字：只当候选。真实街景里它更可能是价格、编号、年份尾数。
            if (num == null || num == 0) return AddressToken(t, AddressKind.NOISE, null, null)
            return AddressToken(t, AddressKind.NUMBER_CANDIDATE, num, null)
        }
        // 混在更长的中文串里的数字（"6X818"、"JQ3"、"传承60载"）判不出来，归噪声
        return if (num != null) AddressToken(t, AddressKind.NOISE, null, null) else null
    }
}

/**
 * 目标侧的地址期望。全部可空：POI 数据里经常只有店名没有门牌，
 * 那时这一路证据整体退出，不产生任何 MATCH/CONFLICT。
 */
class AddressEvidence(
    val houseNumber: Int? = null,
    val buildingNumber: Int? = null,
    val buildingLabel: String? = null,
    val entryNumber: Int? = null,
    val unit: Int? = null
) {
    val empty: Boolean get() =
        houseNumber == null && buildingNumber == null && buildingLabel == null &&
            entryNumber == null && unit == null

    fun compare(tok: AddressToken): AddressOutcome = when (tok.kind) {
        AddressKind.HOUSE_NUMBER -> judge(houseNumber, tok.number)
        AddressKind.BUILDING_NUMBER -> judge(buildingNumber, tok.number)
        AddressKind.ENTRY_NUMBER -> judge(entryNumber, tok.number)
        AddressKind.UNIT -> judge(unit, tok.number)
        AddressKind.BUILDING_LABEL -> when {
            buildingLabel == null || tok.letter == null -> AddressOutcome.UNKNOWN
            tok.letter == buildingLabel.uppercase() -> AddressOutcome.MATCH
            else -> AddressOutcome.CONFLICT
        }
        // 光数字只能"证实"，不能"证伪"：把 101 单独读到时不该推翻店名证据
        AddressKind.NUMBER_CANDIDATE ->
            if (houseNumber != null && houseNumber == tok.number) AddressOutcome.MATCH
            else AddressOutcome.UNKNOWN
        else -> AddressOutcome.UNKNOWN
    }

    private fun judge(expected: Int?, seen: Int?): AddressOutcome = when {
        expected == null || seen == null -> AddressOutcome.UNKNOWN
        expected == seen -> AddressOutcome.MATCH
        else -> AddressOutcome.CONFLICT
    }

    /** 汇总一帧：任何一个明确冲突即冲突；没有冲突且有 MATCH 就算支持。 */
    fun summarize(tokens: List<AddressToken>): AddressOutcome {
        if (empty) return AddressOutcome.UNKNOWN
        val results = tokens.map { compare(it) }
        return when {
            results.contains(AddressOutcome.CONFLICT) -> AddressOutcome.CONFLICT
            results.contains(AddressOutcome.MATCH) -> AddressOutcome.MATCH
            else -> AddressOutcome.UNKNOWN
        }
    }
}

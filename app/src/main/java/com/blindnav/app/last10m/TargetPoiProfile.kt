package com.blindnav.app.last10m

/**
 * 目标 POI 画像（任务书第四节）。
 *
 * 匹配强度分档返回，**由调用方决定哪一档够格锁目标**：
 *  - EXACT       归一化后完全相等；
 *  - ALIAS       命中"够格"的别名（EXPLICIT 或 DERIVED_SAFE）；
 *  - SUBSTRING   名字包含关系且重叠比例达标；
 *  - FUZZY       编辑距离相似度达标，且串长达到下限；
 *  - SUPPORTING  **只算佐证**：过短的派生别名、或只读到名字的一小段碎片。
 *
 * 为什么要单独有 SUPPORTING 这一档：真实数据扫出来身份误接受全部来自
 * "去后缀派生别名 + 包含即算别名"（"抚顺银行"派生出"抚顺"，于是"抚顺火锅"
 * 也以 ALIAS 0.95 够格确认身份），而不是来自模糊阈值。把这类命中降级成佐证、
 * 而不是直接删掉，是因为它们在门口确实有信息量——只是不够格单独确认身份。
 */
data class TargetPoiProfile(
    val poiName: String,
    val normalizedName: String,
    val aliases: List<String>,
    /** 与 [aliases] 一一对应；缺省时按派生规则自动归类（保守：短的一律 WEAK）。 */
    val aliasSources: List<AliasSource> = aliases.map { classifyDerivedAlias(it) },
    val address: String = "",
    val category: String = "",
    val destinationLat: Double? = null,
    val destinationLng: Double? = null,
    val knownEntranceLat: Double? = null,
    val knownEntranceLng: Double? = null,
    val routeBearingDegrees: Double? = null
) {

    /** 够格参与身份锁定的别名（EXPLICIT / DERIVED_SAFE）。 */
    val strongAliases: List<String>
        get() = aliases.filterIndexed { i, _ ->
            aliasSources.getOrNull(i) == AliasSource.EXPLICIT ||
                aliasSources.getOrNull(i) == AliasSource.DERIVED_SAFE
        }

    /** 只算佐证的派生别名。面板要能把它们显示出来，但不参与锁定。 */
    val weakAliases: List<String>
        get() = aliases.filterIndexed { i, _ -> aliasSources.getOrNull(i) == AliasSource.DERIVED_WEAK }

    /** 匹配一次 OCR/VLM 文本。返回 null 表示完全不相关，调用方不应据此改变状态。 */
    fun match(text: String): TargetMatch? {
        val candidate = TextNormalizer.normalize(text)
        if (candidate.isEmpty()) return null
        val candidateStripped = TextNormalizer.stripNoise(candidate)
        if (candidateStripped.isEmpty()) return null

        for (form in matchForms) {
            if (form.normalized.isEmpty()) continue
            if (candidate == form.normalized || candidateStripped == form.normalized) {
                return TargetMatch(form.source, TargetMatchKind.EXACT, 1.0, AliasSource.EXPLICIT)
            }
        }
        // 弱别名先记下来，继续找有没有更强的一档——不能一命中就早退，
        // 否则同一帧里"车主"(WEAK) 会挡住后面可能出现的强命中。
        var weak: TargetMatch? = null
        for (form in aliasForms) {
            if (form.value.isEmpty()) continue
            val contained = candidate.contains(form.value) || candidateStripped.contains(form.value)
            val fragment = form.value.contains(candidate) || form.value.contains(candidateStripped)
            if (!contained && !fragment) continue
            if (form.aliasSource == AliasSource.DERIVED_WEAK) {
                weak = stronger(weak,
                    TargetMatch(form.source, TargetMatchKind.SUPPORTING, 0.60, form.aliasSource))
                continue
            }
            if (contained) {
                // 招牌里含着我们的名字：强证据。
                return TargetMatch(form.source, TargetMatchKind.ALIAS,
                    if (candidate.contains(form.value)) 0.95 else 0.92, form.aliasSource)
            }
            // 只读到名字的一段碎片：够长、或者剩下的部分确实是噪声后缀，才算强证据。
            // "天地壹号"读到"天地壹"就是反例——剩下一个"号"不是噪声，"天地壹"本身是另一家店。
            val observed = if (form.value.contains(candidate)) candidate else candidateStripped
            if (isStrongFragment(form.value, observed)) {
                return TargetMatch(form.source, TargetMatchKind.ALIAS, 0.88, form.aliasSource)
            }
            weak = stronger(weak,
                TargetMatch(form.source, TargetMatchKind.SUPPORTING, 0.60, form.aliasSource))
        }
        for (form in matchForms) {
            if (form.normalized.length < TextNormalizer.FUZZY_MIN_LENGTH) continue
            val longer = maxOf(form.normalized.length, candidateStripped.length)
            if (longer == 0) continue
            if (candidateStripped.contains(form.normalized)) {
                // 招牌上完整地写着我们的名字：强证据。
                return TargetMatch(form.source, TargetMatchKind.SUBSTRING, 0.85, AliasSource.EXPLICIT)
            }
            if (form.normalized.contains(candidateStripped)) {
                // 只读到名字的一段：与别名碎片方向用同一把尺子。
                // 「天地壹号」看到「天地壹」——剩下一个"号"不是噪声，而「天地壹」自己是另一家店。
                if (isStrongFragment(form.normalized, candidateStripped)) {
                    return TargetMatch(form.source, TargetMatchKind.SUBSTRING, 0.85, AliasSource.EXPLICIT)
                }
                weak = stronger(weak,
                    TargetMatch(form.source, TargetMatchKind.SUPPORTING, 0.58, AliasSource.DERIVED_WEAK))
            }
        }
        var bestFuzzy: TargetMatch? = null
        for (form in matchForms + strongAliasForms) {
            val value = form.value
            if (value.length < TextNormalizer.FUZZY_MIN_LENGTH ||
                candidateStripped.length < TextNormalizer.FUZZY_MIN_LENGTH) continue
            val score = TextNormalizer.similarity(value, candidateStripped)
            if (score >= TextNormalizer.FUZZY_THRESHOLD &&
                (bestFuzzy == null || score > bestFuzzy.score)) {
                bestFuzzy = TargetMatch(form.source, TargetMatchKind.FUZZY, score, form.aliasSource)
            }
        }
        // 模糊档与佐证档都不算确认：谁分高返回谁，由 acceptsAsIdentity() 拦住身份锁定。
        return stronger(bestFuzzy, weak)
    }

    private fun stronger(a: TargetMatch?, b: TargetMatch?): TargetMatch? =
        if (a == null) b else if (b == null) a else if (b.score > a.score) b else a

    /** 够格参与"多帧确认"的最低匹配档。FUZZY 与 SUPPORTING 单独出现都不算确认。 */
    fun acceptsAsIdentity(match: TargetMatch): Boolean =
        match.kind != TargetMatchKind.NONE && match.kind != TargetMatchKind.FUZZY &&
            match.kind != TargetMatchKind.SUPPORTING && match.score >= 0.85

    /** 只用于展示"为什么认它"，不参与判定。 */
    fun describe(): String = buildString {
        append("poi=").append(poiName)
        append(" aliases=").append(aliases.joinToString("/").ifEmpty { "-" })
        append(" weak=").append(weakAliases.joinToString("/").ifEmpty { "-" })
    }

    private data class Form(val source: String, val normalized: String, val value: String,
                            val aliasSource: AliasSource)

    private val matchForms: List<Form> = buildList {
        val n = TextNormalizer.normalize(poiName)
        add(Form(poiName, n, TextNormalizer.stripNoise(n), AliasSource.EXPLICIT))
    }

    private val aliasForms: List<Form> = aliases.mapIndexed { i, value ->
        val n = TextNormalizer.normalize(value)
        Form(value, n, TextNormalizer.stripNoise(n), aliasSources.getOrNull(i) ?: classifyDerivedAlias(value))
    }

    private val strongAliasForms: List<Form> = aliasForms.filter {
        it.aliasSource == AliasSource.EXPLICIT || it.aliasSource == AliasSource.DERIVED_SAFE
    }

    companion object {
        /** 碎片至少这么长才够格当身份（2 字碎片是"车主/便利/晋商"这类误接受的主因）。 */
        const val MIN_FRAGMENT_STRONG = 4

        /**
         * 这段碎片够不够格当身份证据。
         *
         * 两种情况下算够格：
         *  1. 碎片本身已经有 [MIN_FRAGMENT_STRONG] 个字以上——到那个长度，
         *     它同时是隔壁店完整名的概率已经很低；
         *  2. 碎片短一点，但**名字里剩下的部分确实是噪声业态词**（店/超市/咖啡/服务中心…），
         *     说明我们看到的就是品牌主名的全部。
         *
         * 反面例子（真实数据里抓到的）：目标「天地壹号」，看到「天地壹」。
         * 剩下的是"号"，不是噪声——「天地壹」自己在语料里就是另一家店。这种必须降级成佐证。
         */
        fun isStrongFragment(name: String, observed: String): Boolean {
            if (observed.isEmpty()) return false
            if (observed.length >= MIN_FRAGMENT_STRONG) return true
            if (observed.length < TextNormalizer.FUZZY_MIN_LENGTH) return false
            val idx = name.indexOf(observed)
            if (idx < 0) return false
            val leftover = name.removeRange(idx, idx + observed.length)
            if (leftover.isBlank()) return true
            return leftover in TextNormalizer.NOISE_SUFFIXES || leftover in TextNormalizer.GENERIC_FRAGMENTS
        }

        /**
         * 派生别名的强度分级。**默认禁止自动产生过短或过泛的别名参与身份锁定。**
         *  - 长度 ≤2 个字：一律 DERIVED_WEAK（"车主""便利""抚顺""晋商"都落在这里）；
         *  - 命中通用词表（银行/超市/咖啡/服务中心…）：DERIVED_WEAK；
         *  - 其余：DERIVED_SAFE。
         * 人工明确给出的别名走 [AliasSource.EXPLICIT]，不受这条限制（见 [build]）。
         */
        fun classifyDerivedAlias(value: String): AliasSource {
            val v = TextNormalizer.stripNoise(TextNormalizer.normalize(value))
            if (v.length <= 2) return AliasSource.DERIVED_WEAK
            if (v in TextNormalizer.GENERIC_FRAGMENTS) return AliasSource.DERIVED_WEAK
            return AliasSource.DERIVED_SAFE
        }

        /**
         * 从 POI 名自动派生别名。规则刻意保守：只去后缀、只处理 ASCII 大小写，
         * 不做分词/拼音/翻译——那些容易造出"看着像但其实不是"的别名，
         * 而假别名会直接推高 Neighbor Door False Acceptance。
         *
         * `extraAliases` 是**人工给的**，按 [AliasSource.EXPLICIT] 处理；
         * 规则派生出来的一律走 [classifyDerivedAlias]。
         */
        fun build(
            poiName: String,
            extraAliases: List<String> = emptyList(),
            address: String = "",
            category: String = "",
            destinationLat: Double? = null,
            destinationLng: Double? = null,
            knownEntranceLat: Double? = null,
            knownEntranceLng: Double? = null,
            routeBearingDegrees: Double? = null
        ): TargetPoiProfile {
            val normalized = TextNormalizer.normalize(poiName)
            val explicit = LinkedHashSet<String>()
            val derived = LinkedHashSet<String>()
            explicit.addAll(extraAliases.filter { it.isNotBlank() }.map { it.trim() })
            TextNormalizer.stripNoise(normalized).takeIf { it.isNotEmpty() && it != normalized }?.let { derived.add(it) }
            if (poiName.isNotBlank()) derived.add(poiName.trim())
            // 括号/间隔号后的分店信息去掉，留主名
            Regex("[（(·・\\-\\s]").find(poiName)?.let { mark ->
                val head = poiName.substring(0, mark.range.first).trim()
                if (head.isNotEmpty()) derived.add(head)
            }
            // 顺序：人工在前、派生在后；同名时人工优先（EXPLICIT 压过 DERIVED）。
            val ordered = ArrayList<String>()
            val sources = ArrayList<AliasSource>()
            for (a in explicit) { ordered.add(a); sources.add(AliasSource.EXPLICIT) }
            for (a in derived) {
                if (ordered.contains(a)) continue
                ordered.add(a); sources.add(classifyDerivedAlias(a))
            }
            val aliases = ordered.filter { it != poiName }.ifEmpty { ordered.toList() }
            val aliasSources = sources.filterIndexed { i, _ -> ordered[i] != poiName }
                .ifEmpty { sources.toList() }
            return TargetPoiProfile(
                poiName = poiName,
                normalizedName = normalized,
                aliases = aliases,
                aliasSources = aliasSources,
                address = address,
                category = category,
                destinationLat = destinationLat,
                destinationLng = destinationLng,
                knownEntranceLat = knownEntranceLat,
                knownEntranceLng = knownEntranceLng,
                routeBearingDegrees = routeBearingDegrees
            )
        }
    }
}

/**
 * 别名的来源与强度。这一档直接决定"能不能靠它确认身份"：
 *  - EXPLICIT      人工/POI 记录明确给出（`aliases = ["星巴克","STARBUCKS"]`）→ 可确认；
 *  - DERIVED_SAFE  规则派生且足够独特（长度 ≥3 且不是通用词）→ 可确认；
 *  - DERIVED_WEAK  规则派生但过短或过泛（"车主""便利""抚顺"）→ **只能当佐证**。
 */
enum class AliasSource { EXPLICIT, DERIVED_SAFE, DERIVED_WEAK }

enum class TargetMatchKind { NONE, EXACT, ALIAS, SUBSTRING, FUZZY, SUPPORTING }

data class TargetMatch(
    val matchedAlias: String,
    val kind: TargetMatchKind,
    val score: Double,
    /** 命中依据是哪一档别名来源；EXACT/SUBSTRING 命中主名时是 EXPLICIT。 */
    val aliasSource: AliasSource = AliasSource.EXPLICIT
)

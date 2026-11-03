package com.blindnav.app.scene

/**
 * 环境感知的记忆与差分（4+1 里的第 4 项 + SceneMemory）。
 *
 * 为什么不是把 [com.blindnav.app.SpeechDeduper] 再套一层：那个只在**字符串完全相同**
 * 时丢重复，所以"前方有人"→"前方有人和自行车"→"前方有人"会连着播三条，
 * 每条都"不重复"。环境播报真正要防的是**语义上没有新信息**，
 * 所以这里按"类别 + 方位"当身份做差分，而不是比文本。
 *
 * 三条原则：
 *  1. 只报变化：与"已经认定存在的那批目标"比，而不是与上一帧比。
 *  2. 闪现不算数：目标要连续 confirmFrames 次出现才算存在，
 *     连续 forgetFrames 次没出现才算离开。检测器单帧误报很常见，
 *     直接播出去就是"狼来了"。
 *  3. 与数据来源无关：本地 YOLO/OCR 和云端 VLM 都归一成 SceneObject 再进来，
 *     断网时只剩本地目标，环境描述自然降级，不影响安全播报。
 */
enum class SceneSide { FRONT, LEFT, RIGHT, UP, DOWN, UNKNOWN }

/** 运动/开关状态。"有人"和"有人正在横穿"必须算两件事。 */
enum class SceneState { STATIC, MOVING, CROSSING, OPENING, CLOSED, UNKNOWN }

data class SceneObject(
    /** 归一化类别：person / bicycle / car / doorway / shopfront / stairs / curb … */
    val category: String,
    val side: SceneSide,
    val state: SceneState = SceneState.STATIC,
    val confidence: Double = 1.0
) {
    /** 身份 = 类别 + 方位；状态不进 key，这样状态变化才看得见。 */
    val identity: String get() = "$category@${side.name}"
}

data class SceneObservation(
    val frameId: Long,
    val timestampMs: Long,
    val objects: List<SceneObject>,
    /** OCR 读到的文字，与 VLM 描述合并后一起进差分 */
    val texts: List<String> = emptyList(),
    /** LOCAL = 只跑端侧模型；CLOUD = 带 VLM 描述。只用于统计，不影响判定。 */
    val source: String = "LOCAL"
)

/** 与"已知存在的那批目标"之间的差异。`hasNews=false` 时策略层应当沉默。 */
data class SceneDelta(
    val appeared: List<SceneObject>,
    val stateChanged: List<Pair<SceneObject, SceneObject>>,
    val disappeared: List<SceneObject>,
    val newTexts: List<String>
) {
    val isEmpty: Boolean
        get() = appeared.isEmpty() && stateChanged.isEmpty() &&
            disappeared.isEmpty() && newTexts.isEmpty()

    /**
     * 值不值得开口：新出现、状态变化、新文字算"新消息"；
     * **单纯消失不算**——人走开了通常不需要专门播报一条，那是噪声的主要来源。
     */
    val hasNews: Boolean
        get() = appeared.isNotEmpty() || stateChanged.isNotEmpty() || newTexts.isNotEmpty()
}

class SceneMemory(
    private val confirmFrames: Int = 2,
    private val forgetFrames: Int = 3
) {
    private class Track(var obj: SceneObject, var hits: Int, var misses: Int, var confirmed: Boolean)

    private val tracks = LinkedHashMap<String, Track>()
    /** 已经认定存在（并因此"说过/不必再说"）的那批目标 */
    private val known = LinkedHashMap<String, SceneObject>()
    private val knownTexts = LinkedHashSet<String>()
    private var lastFrameId = -1L

    fun stableObjects(): List<SceneObject> = tracks.values.filter { it.confirmed }.map { it.obj }

    /**
     * 喂一次观测，返回差分。
     * 第一次观测一定 `hasNews=false`：刚拿到画面时没有"变化"可说，
     * 开场描述应该由用户主动问或场景切换触发，而不是这里硬挤一条。
     */
    fun observe(obs: SceneObservation): SceneDelta {
        if (obs.frameId <= lastFrameId)
            return SceneDelta(emptyList(), emptyList(), emptyList(), emptyList())
        lastFrameId = obs.frameId

        // 同一帧里同一身份只留置信度最高的一条，避免重复计数
        val seen = LinkedHashMap<String, SceneObject>()
        for (o in obs.objects) {
            val prev = seen[o.identity]
            if (prev == null || o.confidence > prev.confidence) seen[o.identity] = o
        }

        for ((id, o) in seen) {
            val t = tracks[id]
            if (t == null) {
                tracks[id] = Track(o, 1, 0, confirmFrames <= 1)
            } else if (t.obj.state != o.state) {
                // 状态变了要重新确认一次，防单帧抖动把"静止"翻成"移动"
                tracks[id] = Track(o, 1, 0, confirmFrames <= 1)
            } else {
                t.obj = o; t.hits++; t.misses = 0
                if (!t.confirmed && t.hits >= confirmFrames) t.confirmed = true
            }
        }
        val gone = ArrayList<String>()
        for ((id, t) in tracks) {
            if (!seen.containsKey(id)) {
                t.misses++
                if (t.misses >= forgetFrames) gone.add(id)
            }
        }
        for (id in gone) tracks.remove(id)

        val stable = stableObjects()
        val stableIds = stable.map { it.identity }.toSet()
        val appeared = stable.filter { !known.containsKey(it.identity) }
        val changed = stable.mapNotNull { cur ->
            val prev = known[cur.identity]
            if (prev != null && prev != cur) prev to cur else null
        }
        val disappeared = known.values.filter { it.identity !in stableIds }
        val newTexts = obs.texts.map { it.trim() }.filter { it.isNotEmpty() && knownTexts.add(it) }

        known.clear()
        for (o in stable) known[o.identity] = o
        return SceneDelta(appeared, changed, disappeared, newTexts)
    }

    fun reset() {
        tracks.clear(); known.clear(); knownTexts.clear(); lastFrameId = -1L
    }
}

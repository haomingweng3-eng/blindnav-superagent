package com.blindnav.app.last10m

/**
 * 统一视觉问答的提示词契约（任务书第 6 节）。
 *
 * 一次调用答完所有事，而不是"找门一次、关系一次、地面一次、门类型一次"——
 * 后者既慢又贵，还会让四个字段来自四张不同的画面，拼出一条根本没同时存在过的证据链。
 *
 * 提示词里写死了三件事，而且与 [VisionResultParser] 的严格校验成对：
 *  1. 只输出 JSON；坐标一律千分比（0–1000，左上原点），这样 Android 与电脑两侧不用换算协议；
 *  2. 不确定就填 null / UNKNOWN —— 低置信度的硬猜在这里就被堵在门口；
 *  3. 禁止输出到达/安全/移动/距离这类结论。违规字段由解析层丢弃并记 violation，
 *     运动与到达决策永远留在 [ArrivalAuthority] 里，不在这里。
 *
 * 单独成文件有两个好处：改措辞时能一眼看到影响面；测试可以断言契约本身没被写歪。
 */
object VisionPrompt {

    const val SYSTEM = "你是视障导航『最后十米』的画面事实提取器。" +
        "只输出一个 JSON 对象，不要解释、不要 Markdown。" +
        "坐标一律用千分比（0-1000，左上原点，[x1,y1,x2,y2]）。" +
        "任何不确定的字段填 null 或 UNKNOWN，禁止猜测。" +
        "禁止输出任何结论性说法：到达/到了/安全/可以通过/放心/往左走/往右走/前进/后退/多少米。" +
        "你只回答『看见了什么』，不回答『能不能走』。"

    /** @param focusDoor 已经锁定入口时，把问题收窄到这一扇，门按钮/门类型才问得准。 */
    fun user(target: TargetPoiProfile, context: VisionContext): String = buildString {
        append("目标店铺名：").append(target.poiName)
        val aliases = target.strongAliases.ifEmpty { target.aliases }
        if (aliases.isNotEmpty()) append("（可接受的别名：").append(aliases.take(4).joinToString("、")).append("）")
        if (context.targetLocked) append("\n注意：系统已经在多帧里确认看到过目标招牌，现在需要判断哪一扇门属于它。")
        if (context.recentTexts.isNotEmpty()) {
            append("\n同一画面附近已读到的文字（供参考，可能有错）：")
                .append(context.recentTexts.takeLast(6).joinToString(" | "))
        }
        if (context.knownDoorBoxes.isNotEmpty()) {
            append("\n系统当前的候选门（千分比）：")
            context.knownDoorBoxes.forEach { append(it.id).append('=').append(it.boxPermille.joinToString(",")).append(' ') }
        }
        if (context.focusOnActiveDoor) {
            append("\n请把门类型与开门按钮的判断只集中在候选门里最可能的那一扇。")
        }
        append("\n只根据图像回答，输出一个 JSON 对象，字段严格如下：")
        append("{\"target_visible\":true/false/null,")
        append("\"target_confidence\":0-1000 或 null,")
        append("\"target_identity\":\"画面里读到的目标名，没有就 null\",")
        append("\"ocr_texts\":[{\"text\":\"...\",\"bbox\":[x1,y1,x2,y2],\"confidence\":0-1000}],")
        append("\"doors\":[{\"id\":\"door_1\",\"bbox\":[x1,y1,x2,y2],\"confidence\":0-1000}],")
        append("\"relations\":[{\"door_id\":\"door_1\",\"relation\":\"SAME_TARGET|NEIGHBOR|UNKNOWN\",\"confidence\":0-1000}],")
        append("\"ground\":{\"visible\":true/false/null,\"step\":true/false/null,\"curb\":true/false/null,\"obstruction\":true/false/null},")
        append("\"door_type\":\"AUTOMATIC_SLIDING|PUSH_PULL|REVOLVING|OPEN_DOORWAY|UNKNOWN\",")
        append("\"button_location\":\"LEFT_OF_DOOR|RIGHT_OF_DOOR|ON_DOOR|UNKNOWN\",")
        append("\"need_view\":\"SIGN|DOOR|STOREFRONT|GROUND|WIDER|UNKNOWN\",")
        append("\"scene_summary\":\"一句中文，只描述画面里有什么，不判断安全，不给指令\"}")
        append("\n规则：")
        append("画面里一个门都没有时 doors 给空数组；")
        append("relations 只能对 doors 里的 id 说话；")
        append("说不清 relation 一律 UNKNOWN，不要为了让流程走下去而编一个；")
        append("step/curb/obstruction 只有看清楚了才给 true/false，看不清给 null。")
    }
}

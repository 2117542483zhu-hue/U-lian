"""体态问题 -> 矫正动作 / 健身房动作 的确定性规则引擎（移植自微信小程序 genPlan/rules.js）。

动作对象字段：
    id        唯一标识
    name      动作名称
    target    针对的体态问题（用于展示）
    category  类别：'矫正'（无器械拉伸/激活） 或 '健身房'（力量训练）
    equipment 器械（健身房动作用；矫正动作为空）
    duration  单次时长 / 次数
    sets      组数
    level     难度
    steps[]   动作步骤
    tips[]    要点提示
    avoid[]   错误示范（这样做会练坏）
"""

from __future__ import annotations

from typing import Any, Dict, List

EXERCISES: Dict[str, Dict[str, Any]] = {
    # ===== 矫正动作（无器械） =====
    "下巴后缩训练": {
        "id": "chin-tuck",
        "name": "下巴后缩训练",
        "target": "头前伸",
        "category": "矫正",
        "equipment": "",
        "duration": "30s",
        "sets": "2组",
        "level": "简单",
        "steps": ["坐直或站直，眼睛平视前方", "下巴水平向后收，像挤出双下巴", "保持5秒后缓慢放松"],
        "tips": ["动作要慢，感受颈后部发力"],
        "avoid": ["不要低头或仰头，只做水平后缩"],
    },
    "上斜方肌拉伸": {
        "id": "upper-trap-stretch",
        "name": "上斜方肌拉伸",
        "target": "头前伸/高低肩",
        "category": "矫正",
        "equipment": "",
        "duration": "30s×2",
        "sets": "2组",
        "level": "简单",
        "steps": ["坐直，一手抓椅子边缘固定肩膀", "另一手扶头，轻轻向对侧下方拉", "感到颈部侧后方牵拉即停，保持30秒"],
        "tips": ["肩膀放松下沉，不要耸肩"],
        "avoid": ["不要用力猛拉头部"],
    },
    "胸小肌拉伸": {
        "id": "chest-stretch",
        "name": "胸小肌拉伸",
        "target": "圆肩",
        "category": "矫正",
        "equipment": "",
        "duration": "30s×2",
        "sets": "2组",
        "level": "简单",
        "steps": ["侧对门框站立，屈肘90度贴住门框", "身体缓慢向前，感受胸前牵拉", "保持30秒后换边"],
        "tips": ["肩膀放松，不要塌腰"],
        "avoid": ["不要耸肩，不要过度挺腰代偿"],
    },
    "肩胛后缩": {
        "id": "scapular-retraction",
        "name": "肩胛后缩",
        "target": "圆肩",
        "category": "矫正",
        "equipment": "",
        "duration": "15次",
        "sets": "3组",
        "level": "简单",
        "steps": ["坐直，双臂自然下垂", "两侧肩胛骨向脊柱方向夹紧", "保持3秒后放松"],
        "tips": ["想象肩胛骨中间夹一支笔"],
        "avoid": ["不要耸肩，不要憋气"],
    },
    "靠墙天使": {
        "id": "wall-angel",
        "name": "靠墙天使",
        "target": "圆肩驼背",
        "category": "矫正",
        "equipment": "",
        "duration": "12次",
        "sets": "3组",
        "level": "中等",
        "steps": ["背靠墙站立，后脑、肩胛、臀部贴墙", "双臂贴墙，像做天使翅膀一样上下滑动", "全程保持贴墙，缓慢完成"],
        "tips": ["腰部离墙一掌内属正常，不要过度挺腰"],
        "avoid": ["手臂贴不到墙时不要强行压，可减小幅度"],
    },
    "臀桥": {
        "id": "glute-bridge",
        "name": "臀桥",
        "target": "骨盆前倾",
        "category": "矫正",
        "equipment": "",
        "duration": "15次",
        "sets": "3组",
        "level": "简单",
        "steps": ["仰卧屈膝，双脚与肩同宽", "收紧臀部向上顶起，肩到膝成直线", "顶端保持2秒后缓慢放下"],
        "tips": ["用臀部发力，不是用腰顶"],
        "avoid": ["不要过度挺腰，顶端不要过伸"],
    },
    "腹式呼吸": {
        "id": "diaphragmatic-breathing",
        "name": "腹式呼吸",
        "target": "骨盆前倾",
        "category": "矫正",
        "equipment": "",
        "duration": "5分钟",
        "sets": "1组",
        "level": "简单",
        "steps": ["仰卧或坐直，一手放腹部", "用鼻吸气，感受腹部鼓起", "用嘴缓慢呼气，腹部自然回落"],
        "tips": ["呼气时间可略长于吸气，帮助放松"],
        "avoid": ["不要耸肩用胸式呼吸"],
    },
    # ===== 健身房力量动作 =====
    "深蹲": {
        "id": "squat",
        "name": "深蹲",
        "target": "骨盆前倾/下肢力量",
        "category": "健身房",
        "equipment": "杠铃/自重",
        "duration": "12次",
        "sets": "4组",
        "level": "中等",
        "steps": ["双脚与肩同宽，脚尖微向外展", "臀部向后下方坐，像坐椅子", "膝盖沿脚尖方向，下蹲至大腿平行地面", "脚跟发力，臀部收紧站起"],
        "tips": ["全程保持背部中立、核心收紧", "膝盖方向始终与脚尖一致"],
        "avoid": ["膝盖内扣", "弓背塌腰", "脚跟离地重心前倾"],
    },
    "罗马尼亚硬拉": {
        "id": "rdl",
        "name": "罗马尼亚硬拉",
        "target": "骨盆前倾/后链",
        "category": "健身房",
        "equipment": "杠铃/哑铃",
        "duration": "10次",
        "sets": "4组",
        "level": "中等",
        "steps": ["双脚与髋同宽，杠铃贴腿", "髋部向后推，背部保持平直", "杠铃沿大腿下放至小腿中段", "臀部发力，髋部前顶站直"],
        "tips": ["感受大腿后侧与臀部拉伸", "杠铃始终贴近身体"],
        "avoid": ["弓背（圆背）", "杠铃远离身体", "膝盖过度弯曲变成深蹲"],
    },
    "臀推": {
        "id": "hip-thrust",
        "name": "臀推",
        "target": "骨盆前倾/臀肌",
        "category": "健身房",
        "equipment": "杠铃+凳",
        "duration": "12次",
        "sets": "4组",
        "level": "中等",
        "steps": ["肩胛骨下缘靠在凳边，杠铃置于髋部", "双脚踩实，臀部发力向上顶", "顶端身体呈直线，挤压臀肌2秒", "缓慢下放"],
        "tips": ["用臀部发力，不是用腰顶", "顶端不要过度挺腰"],
        "avoid": ["腰椎过伸（过度挺腰）", "脖子用力后仰", "行程太短只动一半"],
    },
    "面拉": {
        "id": "face-pull",
        "name": "面拉",
        "target": "头前伸/圆肩",
        "category": "健身房",
        "equipment": "绳索/弹力带",
        "duration": "15次",
        "sets": "3组",
        "level": "简单",
        "steps": ["绳索调至面部高度，双手抓绳", "向面部方向拉，肘部向外展", "肩胛骨向后收，顶端停顿1秒", "缓慢还原"],
        "tips": ["想象肩胛骨中间夹一支笔", "用肩胛发力，不是手臂"],
        "avoid": ["耸肩", "身体大幅后仰借力", "肘部下垂变成下拉"],
    },
    "坐姿划船": {
        "id": "seated-row",
        "name": "坐姿划船",
        "target": "圆肩/背部",
        "category": "健身房",
        "equipment": "划船机/绳索",
        "duration": "12次",
        "sets": "4组",
        "level": "简单",
        "steps": ["坐稳，双脚踩实，腰背挺直", "拉手柄至腹部，肩胛骨后收", "顶端挤压背部1秒", "缓慢还原"],
        "tips": ["躯干保持稳定，不要大幅后仰", "先收肩胛再拉手臂"],
        "avoid": ["圆背", "身体后仰借力", "耸肩"],
    },
    "反向飞鸟": {
        "id": "reverse-fly",
        "name": "反向飞鸟",
        "target": "圆肩/三角肌后束",
        "category": "健身房",
        "equipment": "哑铃",
        "duration": "15次",
        "sets": "3组",
        "level": "简单",
        "steps": ["俯身至背部接近水平，膝盖微屈", "双手持哑铃向身体两侧打开", "顶端停顿1秒，感受肩后侧发力", "缓慢下放还原"],
        "tips": ["手臂微屈，用肩后侧发力", "保持背部平直"],
        "avoid": ["耸肩", "身体上下摆动借力", "用腰部代偿"],
    },
    "山羊挺身": {
        "id": "back-extension",
        "name": "山羊挺身",
        "target": "头前伸/竖脊肌",
        "category": "健身房",
        "equipment": "罗马椅",
        "duration": "12次",
        "sets": "3组",
        "level": "中等",
        "steps": ["固定脚踝，髋部支撑在垫上", "身体下弯至约90度", "用臀部与下背发力抬起至身体直线", "不要过度后仰"],
        "tips": ["发力点在后侧链，不是单纯甩腰", "上升至中立位即可"],
        "avoid": ["过度后伸（超伸）", "用惯性快速甩起"],
    },
    "单臂哑铃划船": {
        "id": "one-arm-row",
        "name": "单臂哑铃划船",
        "target": "高低肩/背部平衡",
        "category": "健身房",
        "equipment": "哑铃+凳",
        "duration": "每侧12次",
        "sets": "3组",
        "level": "中等",
        "steps": ["单膝单手持凳，背部平直", "对侧手持哑铃自然下垂", "上拉至肋部，肘部贴近身体", "缓慢下放，两侧交替"],
        "tips": ["保持躯干不旋转", "用背发力，不是甩手臂"],
        "avoid": ["身体旋转借力", "耸肩", "拉得过猛"],
    },
    "髋外展": {
        "id": "hip-abduction",
        "name": "髋外展",
        "target": "XO型腿/臀中肌",
        "category": "健身房",
        "equipment": "髋外展机",
        "duration": "15次",
        "sets": "3组",
        "level": "简单",
        "steps": ["坐于髋外展机，双腿贴住挡板", "双腿向外打开，感受臀部外侧发力", "打开至最大幅度，停顿1秒", "缓慢还原"],
        "tips": ["用臀中肌发力，不要靠惯性", "躯干保持中立不歪斜"],
        "avoid": ["靠惯性快速开合", "身体歪斜代偿"],
    },
    "平板支撑": {
        "id": "plank",
        "name": "平板支撑",
        "target": "骨盆前倾/核心",
        "category": "健身房",
        "equipment": "自重",
        "duration": "45s",
        "sets": "3组",
        "level": "简单",
        "steps": ["肘部撑地，与肩同宽", "身体从头到脚呈一条直线", "收紧腹部、夹紧臀部", "保持自然呼吸"],
        "tips": ["想象肚脐向后背贴", "不要憋气"],
        "avoid": ["塌腰", "撅臀", "耸肩"],
    },
}

# 问题类型 -> 矫正动作（拉伸/激活）
ISSUE_MAP: Dict[str, List[str]] = {
    "头前伸": ["下巴后缩训练", "上斜方肌拉伸"],
    "圆肩": ["胸小肌拉伸", "肩胛后缩"],
    "圆肩驼背": ["胸小肌拉伸", "肩胛后缩", "靠墙天使"],
    "高低肩": ["上斜方肌拉伸"],
    "骨盆前倾": ["臀桥", "腹式呼吸"],
    "骨盆后倾": ["臀桥", "腹式呼吸"],
    "XO型腿": [],
}

# 问题类型 -> 健身房力量动作（强化无力肌）
GYM_MAP: Dict[str, List[str]] = {
    "头前伸": ["面拉", "坐姿划船", "山羊挺身"],
    "圆肩": ["反向飞鸟", "坐姿划船", "面拉"],
    "圆肩驼背": ["反向飞鸟", "坐姿划船", "面拉"],
    "高低肩": ["单臂哑铃划船"],
    "骨盆前倾": ["罗马尼亚硬拉", "臀推", "深蹲", "平板支撑"],
    "骨盆后倾": ["深蹲", "罗马尼亚硬拉"],
    "XO型腿": ["髋外展"],
}

# 针对特定问题需要避开的动作（避免越练越伤）
AVOID_MAP: Dict[str, List[str]] = {
    "骨盆前倾": ["卷腹", "仰卧起坐"],
    "骨盆后倾": ["过度后伸腰的动作"],
}


def build_plan(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据体态问题列表生成矫正方案。

    返回：{
        exercises:  本次涉及的全部动作对象（去重）
        versions:   {'5分钟课间版': [id...], '15分钟晚间版': [id...], '健身房强化版': [id...]}
        rest_days:  建议休息日（星期几，1=周一 ... 7=周日）
        warnings:   需要避开的动作提示
    }
    """
    corrective_ids: List[str] = []
    gym_ids: List[str] = []
    avoid_set = set()

    for issue in issues:
        issue_type = str(issue.get("type") or "")
        for name in ISSUE_MAP.get(issue_type, []):
            if name not in corrective_ids:
                corrective_ids.append(name)
        for name in GYM_MAP.get(issue_type, []):
            if name not in gym_ids:
                gym_ids.append(name)
        for avoid in AVOID_MAP.get(issue_type, []):
            avoid_set.add(avoid)

    all_ids: List[str] = []
    for name in corrective_ids + gym_ids:
        if name not in all_ids:
            all_ids.append(name)

    exercises = [EXERCISES[name] for name in all_ids if name in EXERCISES]
    corrective = [EXERCISES[name] for name in corrective_ids if name in EXERCISES]
    gym = [EXERCISES[name] for name in gym_ids if name in EXERCISES]

    versions = {
        "5分钟课间版": [e["id"] for e in corrective[:3]],
        "15分钟晚间版": [e["id"] for e in corrective],
        "健身房强化版": [e["id"] for e in gym],
    }

    return {
        "exercises": exercises,
        "versions": versions,
        "rest_days": [2, 5],
        "warnings": [f"注意避开：{'、'.join(sorted(avoid_set))}"] if avoid_set else [],
    }

"""把 encore.moe 抓下来的原始文本，加工成能读的成品。

加工哪几步
----------
1. **还原换行**：抓下来的是 JSON 字符串，换行是 `\\n` 字面量，整篇挤在一行
2. **补翻译**：网站只翻译正文，区块标题和字段名还是英文（`Forte Examination Report`、
   `Stories`、`Birthday:` 这些），替换成中文
3. **清理噪音**：去掉顶部导航、赞助广告、页脚，把剩下的空行压紧
4. **切成两份**：
   - 技能属性版 —— 从"启用 Live2D"之后开始，截到"共鸣回路检验报告"之前
   - 全量版     —— 上面全部 + 背景故事、语音、珍视之物

用法
----
    python tools/process_encore_text.py _raw_1309.txt "1311-心" docs/characters

第一个参数是抓下来的原始文件，第二个是输出目录名，第三个是输出到哪个父目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 网站没翻译的区块标题与字段名。只做"整行完全匹配"的替换，不碰正文。
EN_FIXES = {
    'Skip to content': '跳到内容',
    'WUTHERING WAVES DATABASE': '鸣潮数据库',
    'Home': '首页', 'Characters': '角色', 'Weapons': '武器', 'Echoes': '声骸',
    'Monsters': '怪物', 'Items': '道具', 'Namecards': '名片', 'Titles': '称号',
    'Tower of Adversity': '逆境深塔', 'Endstate Matrix': '终态矩阵',
    'Whimpering Wastes': '呜咽废土', 'Fantasies of the Thousand Gateways': '千门幻想',
    'Stories': '剧情', 'Infos': '信息', 'Diff': '差异', 'Terms': '术语',
    'Exploration Reports': '探索报告', 'Interactive Map': '交互地图',
    'Achievements': '成就', 'About': '关于', 'Settings': '设置',
    'Force Refresh': '强制刷新', 'Overview': '概览', 'Skills Input': '技能输入',
    'Stats & Outfits': '属性与外观', 'Luckdraw': '抽卡', 'Skills': '技能',
    'Bonus': '加成', 'Resonance Chain': '共鸣链', 'Lore': '背景',
    'Back to Characters': '返回角色列表', 'Enable Live2D': '启用 Live2D',
    'Tags': '标签', 'Info': '信息', 'Birthday:': '生日：', 'Gender:': '性别：',
    'Birthplace:': '籍贯：', 'Affiliation:': '所属：', 'Weapon Type:': '武器类型：',
    'CV:': '声优：', 'Forte': '共鸣回路', 'Basic Mechanics': '基础机制',
    'Input Details': '操作说明', 'Stats': '属性', 'Card': '卡片', 'LEVEL': '等级',
    'MATERIALS NEEDED': '所需材料', 'No materials needed for this level': '该等级无需材料',
    'Skins': '皮肤', 'Reset view': '重置视图', 'Export size': '导出尺寸',
    'Preview size': '预览尺寸', 'Download MP4': '下载 MP4', 'Load 3D model': '加载 3D 模型',
    'Mesh and textures are about 30 MB.': '模型与贴图约 30 MB。',
    'SKILL LEVEL': '技能等级', 'MATERIALS': '材料',
    'Video loads when in view': '（视频：滚动到可见时加载）', 'DAMAGE INFO': '伤害信息',
    'Skill Branches': '技能分支', 'Default': '默认',
    'Forte Examination Report': '共鸣回路检验报告', 'Voicelines': '语音',
    'Cherished Items': '珍视之物', 'Required:': '解锁条件：',
    'Privacy': '隐私政策', 'Clear Cache': '清除缓存', 'Made with:': '技术栈：',
}

# 图片 alt 里那些没意义的占位符，直接清掉
DROP_ALT = {
    'Encore', 'Home', 'Characters', 'Weapons', 'Echoes', 'Monsters', 'Items',
    'Namecards', 'Titles', 'Tower of Adversity', 'Endstate Matrix', 'Whimpering Wastes',
    'Fantasies of the Thousand Gateways', 'Stories', 'Infos', 'Diff', 'Terms',
    'Exploration Reports', 'Interactive Map', '3D Viewer', 'WavesLine', 'Achievements',
    'About', 'Lootbar.gg promotion', 'Nuxt', 'Tailwind CSS',
}
ALT_FIXES = {
    'Character Formation Card': '角色养成卡', 'Element': '属性图标', 'Tag Icon': '标签图标',
    'Icon 0': '图标0', 'Icon 1': '图标1', 'Icon 2': '图标2',
    'Property Icon': '属性图标', 'Skill Icon': '技能图标', 'Item': '道具图标',
}

START_ANCHOR = '启用 Live2D'          # 之后才是角色内容，前面全是导航
CUT_ANCHOR = '共鸣回路检验报告'        # 之后是剧情，切技能属性版时截断


def clean(raw: str) -> str:
    """JSON 还原 → 替换英文 → 清占位符 → 压空行。"""
    text = json.loads(raw)

    # 图表占位符
    import re
    def fix_alt(match: re.Match) -> str:
        key = match.group(1).strip()
        if key in DROP_ALT:
            return ''
        if key in ALT_FIXES:
            return f'[{ALT_FIXES[key]}]'
        return match.group(0)

    text = re.sub(r'\[([^\[\]]{1,48})\]', fix_alt, text)

    # 英文标题逐行替换
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        lines.append(EN_FIXES.get(stripped, line))
    text = '\n'.join(lines)

    # 清掉因为删占位符留下的空行，并压紧连续空行
    text = '\n'.join(ln.rstrip() for ln in text.split('\n'))
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def split(text: str) -> tuple[str, str]:
    """切出（技能属性版，全量版）。"""
    start = text.find(START_ANCHOR)
    body = text[start + len(START_ANCHOR):] if start >= 0 else text

    cut = body.find(CUT_ANCHOR)
    skills_only = body[:cut].strip() if cut >= 0 else body.strip()
    return skills_only, body.strip()


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    raw_path = Path(sys.argv[1])
    folder = sys.argv[2]
    out_root = Path(sys.argv[3])

    if not raw_path.exists():
        print(f'找不到原始文件：{raw_path}')
        sys.exit(1)

    text = clean(raw_path.read_text(encoding='utf-8'))
    skills_only, full = split(text)

    out_dir = out_root / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / '技能属性-中文.txt').write_text(skills_only, encoding='utf-8')
    (out_dir / '全量含故事-中文.txt').write_text(full, encoding='utf-8')

    print(f'原始 {len(raw_path.read_text(encoding="utf-8"))} 字符')
    print(f'  → 技能属性版 {len(skills_only)} 字符')
    print(f'  → 全量版     {len(full)} 字符')
    print(f'  写到 {out_dir}/')

    leftover = [ln.strip() for ln in full.split('\n')
                if len(ln.strip()) > 2 and ln.strip().isascii() and ln.strip()[0].isalpha()]
    if leftover:
        print(f'\n⚠️ 还有 {len(set(leftover))} 行纯英文没替换，检查一下：')
        for line in sorted(set(leftover))[:15]:
            print(f'    {line}')
    else:
        print('\n✅ 没有残留的纯英文行')


if __name__ == '__main__':
    main()
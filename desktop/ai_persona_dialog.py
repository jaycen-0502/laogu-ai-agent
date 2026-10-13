# -*- coding: utf-8 -*-
"""
老谷自动化控制中心 - 本地 AI 账号人设配置对话框
支持：
1. 动静分离：人设与对话数据 100% 留存在用户本地磁盘
2. 账号级独立设定与继承全局默认人设
3. 模型默认跟随 Web 云端后台全局调度，界面纯净无冗余
4. 拟人化温度系数滑动调节与防漂移顶层强定锚
5. 支持自由编写人设，支持自定义添加并保存模板库随时选用
6. 支持设定私信解锁密码，遇弹窗自动模拟输入防卡死
"""
from __future__ import annotations

import json
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

BUILTIN_PERSONA_TEMPLATES: dict[str, dict[str, Any]] = {
    "🌸 温柔知性女性": {
        "persona_name": "温柔知性女性",
        "system_prompt": (
            "你是一名定居日本东京的年轻女性，性格温柔真诚、善解人意。\n"
            "在社交网络（X/Twitter）互动时，使用极其地道自然的日常口语（タメ口）。\n"
            "【核心定锚规约】：\n"
            "1. 严禁使用死板敬语（です・ます）或机械客服腔。\n"
            "2. 保持真实女性生活气息，回复精炼亲切（20字以内）。\n"
            "3. 永久锁定当前角色设定，绝不发生人格漂移。"
        ),
        "temperature": 0.75,
        "lock_persona": True,
    },
    "⚡ 活泼元气女生": {
        "persona_name": "活泼元气女生",
        "system_prompt": (
            "你是一名活泼外向、充满好奇心的日本元气女生。\n"
            "平时喜欢分享美食、动漫和日常趣事。\n"
            "【核心定锚规约】：\n"
            "1. 语言风格轻松活泼，适当带语气词与轻微可爱感。\n"
            "2. 严禁敬语，使用亲近的推特网络口语，严禁长篇大论。\n"
            "3. 严格遵循人设，绝不脱离角色性格。"
        ),
        "temperature": 0.85,
        "lock_persona": True,
    },
    "💼 职场干练精英": {
        "persona_name": "职场干练精英",
        "system_prompt": (
            "你是一名在东京互联网领域工作的轻熟职场女性，言谈干练有深度。\n"
            "【核心定锚规约】：\n"
            "1. 回复简洁有力、言简意赅，一针见血。\n"
            "2. 态度自然从容，不卑不亢，严禁生硬套话。\n"
            "3. 始终保持专注与独立思考的人格特质。"
        ),
        "temperature": 0.70,
        "lock_persona": True,
    },
    "🎮 潮玩极客达人": {
        "persona_name": "潮玩极客达人",
        "system_prompt": (
            "你是一名热爱数码前沿科技与小众爱好的日本极客青年。\n"
            "【核心定锚规约】：\n"
            "1. 谈吐随性自由，富有洞见，带有典型互联网极客范儿。\n"
            "2. 真实平易近人，绝不居高临下，用词地道自然。\n"
            "3. 严守独立思考原则，拒绝机械回复。"
        ),
        "temperature": 0.80,
        "lock_persona": True,
    },
}


class AIPersonaConfigDialog(QDialog):
    """本地 AI 账号人设配置对话框"""

    def __init__(
        self,
        controller: Any,
        accounts: Optional[list[Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.accounts = accounts or []
        self._raw_config: dict[str, Any] = {}
        self._current_profile_id: str = "__DEFAULT__"

        self.setWindowTitle("🎭 AI 账号人设配置")
        self.setModal(True)
        self.setMinimumWidth(680)
        self.setMinimumHeight(660)

        self._load_local_data()
        self._init_ui()
        self._populate_account_selector()
        self._switch_profile("__DEFAULT__")

    def _load_local_data(self) -> None:
        """从本地控制器读取人设配置与自定义模板"""
        if hasattr(self.controller, "get_account_personas_config"):
            self._raw_config = self.controller.get_account_personas_config()
        else:
            self._raw_config = {
                "version": 1,
                "default_persona": dict(BUILTIN_PERSONA_TEMPLATES["🌸 温柔知性女性"]),
                "personas": {},
                "custom_templates": {},
            }

        if "custom_templates" not in self._raw_config:
            self._raw_config["custom_templates"] = {}

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 头部区域
        header_frame = QFrame()
        header_frame.setStyleSheet("background: #0F172A; border-bottom: 1px solid #1E293B;")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(24, 18, 24, 16)
        header_layout.setSpacing(6)

        title_lbl = QLabel("🎭 AI 账号人设配置")
        title_lbl.setStyleSheet("color: #F8FAFC; font-size: 17px; font-weight: 700;")
        header_layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            "数据与人设 100% 留存在用户本地磁盘，永久定锚不漂移；"
            "支持自由编写人设并随时保存为个人模板；AI 模型由 Web 后台统一热调度。"
        )
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; line-height: 1.4;")
        desc_lbl.setWordWrap(True)
        header_layout.addWidget(desc_lbl)
        root.addWidget(header_frame)

        # 内容区域
        body = QWidget()
        body.setStyleSheet("background: #F8FAFC;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 18, 24, 16)
        body_layout.setSpacing(14)

        # 账号选择行
        selector_card = QFrame()
        selector_card.setStyleSheet("background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        selector_box = QHBoxLayout(selector_card)
        selector_box.setContentsMargins(14, 10, 14, 10)
        selector_box.setSpacing(12)

        sel_title = QLabel("目标账号：")
        sel_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #1E293B;")
        selector_box.addWidget(sel_title)

        self.account_combo = QComboBox()
        self.account_combo.setStyleSheet(
            "QComboBox { padding: 5px 10px; font-size: 13px; border: 1px solid #CBD5E1; border-radius: 6px; min-width: 280px; }"
        )
        self.account_combo.currentIndexChanged.connect(self._on_account_changed)
        selector_box.addWidget(self.account_combo)
        selector_box.addStretch(1)

        self.badge_lbl = QLabel("全局基准")
        self.badge_lbl.setStyleSheet(
            "QLabel { background: #EEF2FF; color: #4F46E5; border: 1px solid #C7D2FE; border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: 600; }"
        )
        selector_box.addWidget(self.badge_lbl)
        body_layout.addWidget(selector_card)

        # 配置表单卡片
        form_card = QFrame()
        form_card.setStyleSheet("background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(16, 14, 16, 14)
        form_layout.setSpacing(12)

        # 1. 角色标签与模板库操作
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel("角色标签 / 称谓：")
        name_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        name_row.addWidget(name_lbl)

        self.persona_name_edit = QLineEdit()
        self.persona_name_edit.setPlaceholderText("可自由编写，如：温柔知性学姐、活泼萌妹、冷艳御姐...")
        self.persona_name_edit.setStyleSheet(
            "QLineEdit { padding: 5px 8px; border: 1px solid #CBD5E1; border-radius: 5px; font-size: 12px; }"
        )
        name_row.addWidget(self.persona_name_edit, 1)

        self.template_btn = QPushButton("📋 选用模板 ▾")
        self.template_btn.setStyleSheet(
            "QPushButton { background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 5px; padding: 5px 10px; font-size: 12px; font-weight: 600; color: #0F172A; }"
            "QPushButton:hover { background: #E2E8F0; }"
        )
        self._init_template_menu()
        name_row.addWidget(self.template_btn)

        self.save_template_btn = QPushButton("➕ 存为我的模板")
        self.save_template_btn.setStyleSheet(
            "QPushButton { background: #EEF2FF; border: 1px solid #C7D2FE; border-radius: 5px; padding: 5px 10px; font-size: 12px; font-weight: 600; color: #4338CA; }"
            "QPushButton:hover { background: #E0E7FF; }"
        )
        self.save_template_btn.setToolTip("将当前编写的人设保存到个人模板库中，易于直接调取")
        self.save_template_btn.clicked.connect(self._on_save_as_custom_template)
        name_row.addWidget(self.save_template_btn)

        form_layout.addLayout(name_row)

        # 2. 拟人度调节
        temp_row = QHBoxLayout()
        temp_row.setSpacing(10)
        temp_lbl = QLabel("拟人度 / 自由度：")
        temp_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        temp_row.addWidget(temp_lbl)

        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(20, 100)
        self.temp_slider.setValue(75)
        self.temp_slider.setSingleStep(5)
        self.temp_slider.valueChanged.connect(self._on_temp_changed)
        temp_row.addWidget(self.temp_slider, 1)

        self.temp_val_lbl = QLabel("0.75 (平衡生动)")
        self.temp_val_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #2563EB; min-width: 110px;")
        temp_row.addWidget(self.temp_val_lbl)
        form_layout.addLayout(temp_row)

        # 3. 私信密码输入（选填，用于进入私信安全弹窗时模拟输入解锁）
        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(8)
        pwd_lbl = QLabel("私信安全密码：")
        pwd_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        pwd_row.addWidget(pwd_lbl)

        self.dm_password_edit = QLineEdit()
        self.dm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.dm_password_edit.setPlaceholderText("选填；进入私信遇弹窗要求输入密码/PIN时自动模拟打字解锁")
        self.dm_password_edit.setStyleSheet(
            "QLineEdit { padding: 5px 8px; border: 1px solid #CBD5E1; border-radius: 5px; font-size: 12px; }"
        )
        pwd_row.addWidget(self.dm_password_edit, 1)

        self.toggle_dm_pwd_btn = QPushButton("👁️")
        self.toggle_dm_pwd_btn.setFixedSize(32, 28)
        self.toggle_dm_pwd_btn.setToolTip("查看/隐藏私信密码")
        self.toggle_dm_pwd_btn.clicked.connect(self._toggle_dm_pwd_visibility)
        pwd_row.addWidget(self.toggle_dm_pwd_btn)
        form_layout.addLayout(pwd_row)

        # 3.5 回关私信打招呼话术（选填，用于通知栏检测到回关博主时自动私信问候）
        greeting_row = QHBoxLayout()
        greeting_row.setSpacing(8)
        greeting_lbl = QLabel("回关打招呼话术：")
        greeting_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        greeting_row.addWidget(greeting_lbl)

        self.followback_greeting_edit = QLineEdit()
        self.followback_greeting_edit.setPlaceholderText("选填，支持 {name} 占位符；留空则由 AI 结合人设自动生成")
        self.followback_greeting_edit.setStyleSheet(
            "QLineEdit { padding: 5px 8px; border: 1px solid #CBD5E1; border-radius: 5px; font-size: 12px; }"
        )
        greeting_row.addWidget(self.followback_greeting_edit, 1)
        form_layout.addLayout(greeting_row)

        # 4. 防漂移定锚复选框
        self.lock_check = QCheckBox("启用顶层强指令定锚 (推荐：每次对话均强制注入人设底线，杜绝人格漂移)")
        self.lock_check.setChecked(True)
        self.lock_check.setStyleSheet("QCheckBox { font-size: 12px; color: #059669; font-weight: 600; }")
        form_layout.addWidget(self.lock_check)

        # 5. 人设 System 提示词输入框
        prompt_title_row = QHBoxLayout()
        prompt_lbl = QLabel("核心人设 System 提示词 (定义角色背景、性格特质与口吻规约，支持自由编辑)：")
        prompt_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        prompt_title_row.addWidget(prompt_lbl)
        prompt_title_row.addStretch(1)
        form_layout.addLayout(prompt_title_row)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("请输入该角色的系统人设定锚提示词，支持完全自由输入自定义内容...")
        self.prompt_edit.setStyleSheet(
            "QPlainTextEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 8px; font-size: 12px; background: #FFFFFF; color: #0F172A; line-height: 1.4; }"
        )
        self.prompt_edit.setMinimumHeight(190)
        form_layout.addWidget(self.prompt_edit)

        body_layout.addWidget(form_card)
        root.addWidget(body)

        # 底部操作栏
        footer_frame = QFrame()
        footer_frame.setStyleSheet("background: #FFFFFF; border-top: 1px solid #E2E8F0;")
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(24, 12, 24, 12)
        footer_layout.setSpacing(12)

        self.status_lbl = QLabel("就绪 · 本地即改即生效 · 模型由 Web 后台集中调度")
        self.status_lbl.setStyleSheet("color: #64748B; font-size: 12px;")
        footer_layout.addWidget(self.status_lbl)
        footer_layout.addStretch(1)

        self.save_btn = QPushButton("💾 保存并立即生效")
        self.save_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: #FFFFFF; font-weight: 700; font-size: 13px; border-radius: 6px; padding: 7px 18px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self.save_btn.clicked.connect(self._on_save_clicked)
        footer_layout.addWidget(self.save_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setStyleSheet(
            "QPushButton { background: #F1F5F9; color: #475569; font-weight: 600; font-size: 13px; border-radius: 6px; padding: 7px 16px; border: 1px solid #CBD5E1; }"
            "QPushButton:hover { background: #E2E8F0; }"
        )
        self.close_btn.clicked.connect(self.accept)
        footer_layout.addWidget(self.close_btn)

        root.addWidget(footer_frame)

    def _toggle_dm_pwd_visibility(self) -> None:
        if self.dm_password_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.dm_password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_dm_pwd_btn.setText("🔒")
        else:
            self.dm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_dm_pwd_btn.setText("👁️")

    def _init_template_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: #FFFFFF; border: 1px solid #CBD5E1; padding: 4px; font-size: 12px; }")

        custom_templates = self._raw_config.get("custom_templates") or {}
        if custom_templates:
            menu.addSection("⭐ 我的自定义模板")
            for key in custom_templates:
                action = menu.addAction(f"📌 {key}")
                action.triggered.connect(lambda checked=False, k=key: self._apply_custom_template(k))

            del_menu = menu.addMenu("🗑️ 删除自定义模板")
            for key in custom_templates:
                del_act = del_menu.addAction(f"删除【{key}】")
                del_act.triggered.connect(lambda checked=False, k=key: self._delete_custom_template(k))

            menu.addSeparator()

        menu.addSection("💡 精选内置模板")
        for key in BUILTIN_PERSONA_TEMPLATES:
            action = menu.addAction(key)
            action.triggered.connect(lambda checked=False, k=key: self._apply_builtin_template(k))

        self.template_btn.setMenu(menu)

    def _populate_account_selector(self) -> None:
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem("🌟 全局默认人设 (未单独设定时继承)", "__DEFAULT__")

        for acc in self.accounts:
            pid = getattr(acc, "profile_id", None) or getattr(acc, "id", "")
            pname = getattr(acc, "profile_name", "") or getattr(acc, "name", "") or pid
            username = getattr(acc, "x_username", "")
            label = f"👤 {pname}"
            if username:
                label += f" (@{username})"
            label += f" [ID: {pid}]"
            self.account_combo.addItem(label, str(pid))

        self.account_combo.blockSignals(False)

    def _on_account_changed(self, index: int) -> None:
        target_pid = str(self.account_combo.itemData(index) or "__DEFAULT__")
        self._switch_profile(target_pid)

    def _switch_profile(self, profile_id: str) -> None:
        self._current_profile_id = profile_id
        if profile_id == "__DEFAULT__":
            self.badge_lbl.setText("全局基准")
            self.badge_lbl.setStyleSheet(
                "QLabel { background: #EEF2FF; color: #4F46E5; border: 1px solid #C7D2FE; border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: 600; }"
            )
            data = self._raw_config.get("default_persona") or dict(BUILTIN_PERSONA_TEMPLATES["🌸 温柔知性女性"])
        else:
            self.badge_lbl.setText("独立定制")
            self.badge_lbl.setStyleSheet(
                "QLabel { background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: 600; }"
            )
            personas = self._raw_config.get("personas") or {}
            data = personas.get(profile_id)
            if not data:
                # 默认继承全局基准
                data = dict(self._raw_config.get("default_persona") or BUILTIN_PERSONA_TEMPLATES["🌸 温柔知性女性"])

        self.persona_name_edit.setText(str(data.get("persona_name", "") or ""))
        self.prompt_edit.setPlainText(str(data.get("system_prompt", "") or ""))
        self.dm_password_edit.setText(str(data.get("dm_password", "") or ""))
        self.followback_greeting_edit.setText(str(data.get("custom_followback_greeting", "") or ""))
        temp = float(data.get("temperature", 0.75) or 0.75)
        self.temp_slider.setValue(int(round(temp * 100)))
        self._on_temp_changed(int(round(temp * 100)))
        self.lock_check.setChecked(bool(data.get("lock_persona", True)))

        self.status_lbl.setText(f"已载入【{self.account_combo.currentText()}】配置")

    def _on_temp_changed(self, value: int) -> None:
        val_float = round(value / 100.0, 2)
        desc = "严谨克制" if val_float < 0.6 else ("平衡生动" if val_float <= 0.8 else "高度活跃")
        self.temp_val_lbl.setText(f"{val_float:.2f} ({desc})")

    def _apply_builtin_template(self, template_key: str) -> None:
        tmpl = BUILTIN_PERSONA_TEMPLATES.get(template_key)
        if not tmpl:
            return
        self.persona_name_edit.setText(tmpl["persona_name"])
        self.prompt_edit.setPlainText(tmpl["system_prompt"])
        t_val = int(round(float(tmpl["temperature"]) * 100))
        self.temp_slider.setValue(t_val)
        self.lock_check.setChecked(bool(tmpl["lock_persona"]))
        self.status_lbl.setText(f"已选用模板【{template_key}】，可自由修改，点击保存生效")

    def _apply_custom_template(self, template_key: str) -> None:
        customs = self._raw_config.get("custom_templates") or {}
        tmpl = customs.get(template_key)
        if not tmpl:
            return
        self.persona_name_edit.setText(tmpl.get("persona_name", template_key))
        self.prompt_edit.setPlainText(tmpl.get("system_prompt", ""))
        t_val = int(round(float(tmpl.get("temperature", 0.75)) * 100))
        self.temp_slider.setValue(t_val)
        self.lock_check.setChecked(bool(tmpl.get("lock_persona", True)))
        self.status_lbl.setText(f"已选用个人模板【{template_key}】，可继续微调")

    def _on_save_as_custom_template(self) -> None:
        name_hint = self.persona_name_edit.text().strip() or "我的新模板"
        prompt_content = self.prompt_edit.toPlainText().strip()
        if not prompt_content:
            QMessageBox.warning(self, "提示", "请先在提示词框中输入该人设的具体设定，然后再保存为模板！")
            return

        tmpl_name, ok = QInputDialog.getText(
            self,
            "存为我的模板",
            "请输入自定义模板名称：",
            QLineEdit.EchoMode.Normal,
            name_hint,
        )
        if not ok or not tmpl_name.strip():
            return

        clean_name = tmpl_name.strip()
        if "custom_templates" not in self._raw_config or not isinstance(self._raw_config["custom_templates"], dict):
            self._raw_config["custom_templates"] = {}

        self._raw_config["custom_templates"][clean_name] = {
            "persona_name": self.persona_name_edit.text().strip() or clean_name,
            "system_prompt": prompt_content,
            "temperature": round(self.temp_slider.value() / 100.0, 2),
            "lock_persona": self.lock_check.isChecked(),
        }

        if hasattr(self.controller, "save_account_personas_config"):
            self.controller.save_account_personas_config(self._raw_config)

        self._init_template_menu()
        self.status_lbl.setText(f"✅ 自定义模板【{clean_name}】已成功存入本地库！")
        self.status_lbl.setStyleSheet("color: #059669; font-weight: 600; font-size: 12px;")

    def _delete_custom_template(self, template_key: str) -> None:
        customs = self._raw_config.get("custom_templates") or {}
        if template_key in customs:
            del customs[template_key]
            if hasattr(self.controller, "save_account_personas_config"):
                self.controller.save_account_personas_config(self._raw_config)
            self._init_template_menu()
            self.status_lbl.setText(f"🗑️ 已删除自定义模板【{template_key}】")

    def _on_save_clicked(self) -> None:
        current_data = {
            "persona_name": self.persona_name_edit.text().strip(),
            "system_prompt": self.prompt_edit.toPlainText().strip(),
            "dm_password": self.dm_password_edit.text().strip(),
            "custom_followback_greeting": self.followback_greeting_edit.text().strip(),
            "custom_model": "",  # 默认由 Web 后台集中调度
            "temperature": round(self.temp_slider.value() / 100.0, 2),
            "lock_persona": self.lock_check.isChecked(),
        }

        if "personas" not in self._raw_config or not isinstance(self._raw_config["personas"], dict):
            self._raw_config["personas"] = {}

        if self._current_profile_id == "__DEFAULT__":
            self._raw_config["default_persona"] = current_data
        else:
            self._raw_config["personas"][self._current_profile_id] = current_data

        saved_ok = False
        if hasattr(self.controller, "save_account_personas_config"):
            saved_ok = self.controller.save_account_personas_config(self._raw_config)
        else:
            saved_ok = True

        if saved_ok:
            self.status_lbl.setText("✅ 配置已安全保存至本地磁盘，下一次交互立即套用！")
            self.status_lbl.setStyleSheet("color: #059669; font-weight: 600; font-size: 12px;")
        else:
            self.status_lbl.setText("⚠️ 本地文件写入异常，请检查磁盘写权限！")
            self.status_lbl.setStyleSheet("color: #DC2626; font-weight: 600; font-size: 12px;")

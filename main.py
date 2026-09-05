"""
TK全能机器人 - AstrBot 插件
功能：用户系统+权限、AI聊天、文件管理+全文搜索、存储、实用工具
交互式命令：先发命令，机器人提示后再发内容
"""
import os
import json
import hashlib
import time
import random
import sqlite3
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "tkbot.db")
FILES_DIR = os.path.join(DATA_DIR, "files")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FILES_DIR, exist_ok=True)

# 交互状态：{user_id: {"cmd": "register", "step": "password", "data": {}}}
user_states = {}


# ==================== 数据库 ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id TEXT UNIQUE NOT NULL,
        username TEXT,
        nickname TEXT,
        password_hash TEXT,
        second_password_hash TEXT,
        role TEXT DEFAULT 'user',
        permissions TEXT DEFAULT '[]',
        register_time INTEGER,
        last_login INTEGER,
        status TEXT DEFAULT 'active'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        file_name TEXT,
        file_type TEXT,
        file_size INTEGER,
        file_path TEXT,
        content TEXT,
        upload_time INTEGER,
        tags TEXT DEFAULT '[]'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS storage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        key TEXT,
        value TEXT,
        update_time INTEGER,
        UNIQUE(user_id, key)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS login_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        code TEXT,
        expire_time INTEGER,
        used INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()


init_db()


# ==================== 工具函数 ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_user(user_id: str) -> Optional[dict]:
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE tg_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def check_permission(user: dict, perm: str) -> bool:
    if user["role"] in ("super_admin", "owner"):
        return True
    if user["role"] == "admin":
        perms = json.loads(user.get("permissions", "[]"))
        return perm in perms or "all" in perms
    return False


def is_admin(user: dict) -> bool:
    return user["role"] in ("super_admin", "admin", "owner")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f} KB"
    else:
        return f"{size/(1024*1024):.1f} MB"


def set_state(user_id: str, cmd: str, step: str, data: dict = None):
    user_states[user_id] = {"cmd": cmd, "step": step, "data": data or {}}


def clear_state(user_id: str):
    user_states.pop(user_id, None)


# AI 配置
AI_CONFIG_PATH = os.path.join(DATA_DIR, "ai_config.json")
AI_API_URL = "https://api.deepseek.com/v1/chat/completions"
AI_MODEL = "deepseek-chat"
AI_API_KEY = ""

FREE_APIS = [
    {"url": "https://free.churchless.tech/v1/chat/completions", "model": "gpt-3.5-turbo", "key": ""},
]

if os.path.exists(AI_CONFIG_PATH):
    try:
        with open(AI_CONFIG_PATH) as f:
            _cfg = json.load(f)
        AI_API_KEY = _cfg.get("api_key", "")
        AI_API_URL = _cfg.get("api_url", AI_API_URL)
        AI_MODEL = _cfg.get("model", AI_MODEL)
    except:
        pass


async def ai_chat(message: str) -> str:
    import aiohttp
    apis = []
    if AI_API_KEY:
        apis.append({"url": AI_API_URL, "model": AI_MODEL, "key": AI_API_KEY})
    apis.extend(FREE_APIS)
    for api in apis:
        try:
            headers = {"Content-Type": "application/json"}
            if api["key"]:
                headers["Authorization"] = f"Bearer {api['key']}"
            payload = {
                "model": api["model"],
                "messages": [{"role": "user", "content": message}],
                "temperature": 0.7,
                "max_tokens": 1000
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(api["url"], json=payload, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
        except Exception as e:
            continue
    return "AI 服务暂时不可用，请稍后再试，或联系管理员配置 API Key"


async def download_and_save_file(event, user_id, file_id, file_name, file_type, file_size, tags=""):
    """下载文件并保存到数据库"""
    try:
        bot = event.context.get_bot()
        file_obj = await bot.get_file(file_id=file_id)
        file_data = await file_obj.download_as_bytearray()
        now = int(time.time())
        safe_name = f"{now}_{hashlib.md5(file_name.encode()).hexdigest()[:8]}_{file_name}"
        file_path = os.path.join(FILES_DIR, safe_name)
        with open(file_path, 'wb') as wf:
            wf.write(file_data)
        content = ""
        if file_type.startswith("text/") or file_name.endswith(('.txt', '.md', '.csv', '.json', '.py', '.js', '.html', '.css', '.log')):
            try:
                content = file_data.decode('utf-8', errors='ignore')[:50000]
            except:
                pass
        conn = get_db()
        conn.execute("""INSERT INTO files (user_id, file_name, file_type, file_size, file_path, content, upload_time, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, file_name, file_type, file_size, file_path, content, now, tags))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        return False


# ==================== 插件主类 ====================
class TKBotPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("TK全能机器人插件已加载")

    def terminate(self):
        logger.info("TK全能机器人插件已卸载")

    # ==================== 交互式消息监听器 ====================
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_interactive_message(self, event: AstrMessageEvent):
        """处理交互式命令的后续输入"""
        global AI_API_KEY, AI_API_URL, AI_MODEL
        user_id = str(event.get_sender_id())
        text = event.message_str.strip()

        # 如果用户发了新命令（以 / 开头），取消当前交互
        if text.startswith("/"):
            clear_state(user_id)
            return

        state = user_states.get(user_id)
        if not state:
            return  # 不在交互中，不处理

        cmd = state["cmd"]
        step = state["step"]

        # ==================== register 交互 ====================
        if cmd == "register":
            if step == "password":
                if len(text) < 4:
                    yield event.plain_result("密码至少4位，请重新输入")
                    return
                state["data"]["password"] = text
                state["step"] = "secondpass"
                yield event.plain_result("请输入二级密码（不需要请发 跳过）")
                return
            elif step == "secondpass":
                password = state["data"]["password"]
                second_password = "" if text in ("跳过", "skip", "不用", "无") else text
                if get_user(user_id):
                    clear_state(user_id)
                    yield event.plain_result("你已经注册过了，用 /login 登录")
                    return
                now = int(time.time())
                # 安全获取用户名
                username = ""
                try:
                    msg_obj = event.message_obj
                    if hasattr(msg_obj, 'from_user') and msg_obj.from_user:
                        username = getattr(msg_obj.from_user, 'username', '') or ""
                except:
                    pass
                conn = get_db()
                conn.execute("""INSERT INTO users (tg_id, username, nickname, password_hash, second_password_hash, role, register_time, last_login)
                    VALUES (?, ?, ?, ?, ?, 'user', ?, ?)""",
                    (user_id, username, event.get_sender_name(),
                     hash_password(password), hash_password(second_password) if second_password else "",
                     now, now))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"注册成功！昵称：{event.get_sender_name()}\n用 /login 登录")
                return

        # ==================== login 交互 ====================
        if cmd == "login":
            if step == "password":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("你还没注册，用 /register 注册")
                    return
                if user["password_hash"] != hash_password(text):
                    yield event.plain_result("密码错误，请重新输入")
                    return
                code = str(random.randint(100000, 999999))
                now = int(time.time())
                conn = get_db()
                conn.execute("INSERT INTO login_codes (user_id, code, expire_time) VALUES (?, ?, ?)",
                    (user_id, code, now + 300))
                conn.execute("UPDATE users SET last_login=? WHERE tg_id=?", (now, user_id))
                conn.commit()
                conn.close()
                state["step"] = "verify"
                yield event.plain_result(f"登录验证码：{code}\n5分钟内有效，请输入验证码")
                return
            elif step == "verify":
                now = int(time.time())
                conn = get_db()
                record = conn.execute("SELECT * FROM login_codes WHERE user_id=? AND code=? AND used=0 AND expire_time>?",
                    (user_id, text, now)).fetchone()
                if not record:
                    conn.close()
                    yield event.plain_result("验证码错误或已过期，请重新输入")
                    return
                conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (record["id"],))
                conn.commit()
                conn.close()
                user = get_user(user_id)
                role_text = {"super_admin": "超级管理员", "admin": "管理员", "user": "普通用户", "owner": "创建者"}.get(user["role"], "普通用户")
                clear_state(user_id)
                yield event.plain_result(f"登录成功！身份：{role_text}\n用 /help 查看所有命令")
                return

        # ==================== secondpass 交互 ====================
        if cmd == "secondpass":
            user = get_user(user_id)
            if not user:
                clear_state(user_id)
                yield event.plain_result("请先登录")
                return
            if not user["second_password_hash"]:
                clear_state(user_id)
                yield event.plain_result("你没有设置二级密码，跳过")
                return
            if user["second_password_hash"] == hash_password(text):
                clear_state(user_id)
                yield event.plain_result("二级密码验证通过")
            else:
                yield event.plain_result("二级密码错误，请重新输入")
            return

        # ==================== ai 交互 ====================
        if cmd == "ai":
            if step == "question":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                clear_state(user_id)
                yield event.plain_result("思考中...")
                answer = await ai_chat(text)
                yield event.plain_result(f"AI 回答：\n{answer}")
                return

        # ==================== setai 交互 ====================
        if cmd == "setai":
            user = get_user(user_id)
            if not user or not check_permission(user, "manage_ai"):
                clear_state(user_id)
                yield event.plain_result("权限不足")
                return
            if step == "api_key":
                state["data"]["api_key"] = text
                state["step"] = "api_url"
                yield event.plain_result("请输入 API 地址（默认用 DeepSeek，直接发 默认）")
                return
            elif step == "api_url":
                state["data"]["api_url"] = AI_API_URL if text in ("默认", "default", "") else text
                state["step"] = "model"
                yield event.plain_result("请输入模型名（默认 deepseek-chat，直接发 默认）")
                return
            elif step == "model":
                api_key = state["data"]["api_key"]
                api_url = state["data"]["api_url"]
                model = AI_MODEL if text in ("默认", "default", "") else text
                AI_API_KEY = api_key
                AI_API_URL = api_url
                AI_MODEL = model
                config = {"api_key": api_key, "api_url": api_url, "model": model}
                with open(AI_CONFIG_PATH, "w") as f:
                    json.dump(config, f)
                clear_state(user_id)
                yield event.plain_result(f"AI API 已设置\n模型：{model}\n地址：{api_url}")
                return

        # ==================== upload 交互 ====================
        if cmd == "upload":
            if step == "file":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                # 尝试获取消息中的文件
                saved = False
                try:
                    msg_obj = event.message_obj
                    if hasattr(msg_obj, 'document') and msg_obj.document:
                        doc = msg_obj.document
                        saved = await download_and_save_file(
                            event, user_id, doc.file_id,
                            getattr(doc, 'file_name', 'document'),
                            getattr(doc, 'mime_type', 'application/octet-stream'),
                            getattr(doc, 'file_size', 0),
                            state["data"].get("tags", "")
                        )
                    if not saved and hasattr(msg_obj, 'photo') and msg_obj.photo:
                        photo = msg_obj.photo[-1]
                        saved = await download_and_save_file(
                            event, user_id, photo.file_id,
                            f"photo_{int(time.time())}.jpg",
                            "image/jpeg",
                            getattr(photo, 'file_size', 0),
                            state["data"].get("tags", "")
                        )
                except Exception as e:
                    logger.error(f"处理上传文件失败: {e}")
                if saved:
                    clear_state(user_id)
                    yield event.plain_result("文件上传成功！\n用 /search 关键词 搜索文件内容\n用 /files 查看已上传文件")
                else:
                    yield event.plain_result("没有检测到文件，请发送或转发文件（不需要再发 /upload）")
                return
            if step == "tags":
                state["data"]["tags"] = text
                state["step"] = "file"
                yield event.plain_result("请发送或转发要上传的文件")
                return

        # ==================== search 交互 ====================
        if cmd == "search":
            if step == "keyword":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                keyword = text
                conn = get_db()
                results = conn.execute("""SELECT * FROM files WHERE user_id=? AND (file_name LIKE ? OR content LIKE ? OR tags LIKE ?)
                    ORDER BY upload_time DESC LIMIT 10""",
                    (user_id, f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")).fetchall()
                conn.close()
                clear_state(user_id)
                if not results:
                    yield event.plain_result(f"没有找到包含「{keyword}」的文件")
                    return
                text_out = f"搜索「{keyword}」结果（{len(results)}个）\n"
                for f in results:
                    context = ""
                    if f["content"]:
                        idx = f["content"].lower().find(keyword.lower())
                        if idx >= 0:
                            start = max(0, idx - 30)
                            end = min(len(f["content"]), idx + len(keyword) + 30)
                            context = f["content"][start:end].replace("\n", " ")
                    text_out += f"#{f['id']} {f['file_name']} ({format_size(f['file_size'])})\n"
                    if context:
                        text_out += f"  ...{context}...\n"
                yield event.plain_result(text_out)
                return

        # ==================== delfile 交互 ====================
        if cmd == "delfile":
            if step == "file_id":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                try:
                    file_id = int(text)
                except:
                    yield event.plain_result("文件ID必须是数字，请重新输入")
                    return
                conn = get_db()
                f = conn.execute("SELECT * FROM files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone()
                if not f:
                    conn.close()
                    clear_state(user_id)
                    yield event.plain_result("文件不存在或无权删除")
                    return
                if os.path.exists(f["file_path"]):
                    os.remove(f["file_path"])
                conn.execute("DELETE FROM files WHERE id=?", (file_id,))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"已删除文件：{f['file_name']}")
                return

        # ==================== set 存储交互 ====================
        if cmd == "set":
            if step == "key":
                state["data"]["key"] = text
                state["step"] = "value"
                yield event.plain_result("请输入要存储的值")
                return
            elif step == "value":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                key = state["data"]["key"]
                value = text
                now = int(time.time())
                conn = get_db()
                conn.execute("""INSERT INTO storage (user_id, key, value, update_time) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET value=?, update_time=?""",
                    (user_id, key, value, now, value, now))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"已保存：{key} = {value}")
                return

        # ==================== get 存储交互 ====================
        if cmd == "get":
            if step == "key":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                conn = get_db()
                record = conn.execute("SELECT * FROM storage WHERE user_id=? AND key=?",
                    (user_id, text)).fetchone()
                conn.close()
                clear_state(user_id)
                if not record:
                    yield event.plain_result(f"没有找到键「{text}」")
                    return
                yield event.plain_result(f"{record['key']} = {record['value']}")
                return

        # ==================== del 存储交互 ====================
        if cmd == "del":
            if step == "key":
                user = get_user(user_id)
                if not user:
                    clear_state(user_id)
                    yield event.plain_result("请先登录")
                    return
                conn = get_db()
                conn.execute("DELETE FROM storage WHERE user_id=? AND key=?", (user_id, text))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"已删除：{text}")
                return

        # ==================== weather 交互 ====================
        if cmd == "weather":
            if step == "city":
                import aiohttp
                city = text
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"https://wttr.in/{city}?format=j1", timeout=10) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                current = data["current_condition"][0]
                                desc = current["lang_zh"][0]["value"] if current.get("lang_zh") else current["weatherDesc"][0]["value"]
                                temp = current["temp_C"]
                                feels = current["FeelsLikeC"]
                                humidity = current["humidity"]
                                wind = current["windspeedKmph"]
                                clear_state(user_id)
                                yield event.plain_result(f"{city}天气\n天气：{desc}\n温度：{temp}°C（体感{feels}°C）\n湿度：{humidity}%\n风速：{wind} km/h")
                                return
                except:
                    pass
                clear_state(user_id)
                yield event.plain_result("天气查询失败")
                return

        # ==================== translate 交互 ====================
        if cmd == "translate":
            if step == "text":
                clear_state(user_id)
                result = await ai_chat(f"请把下面的文字翻译成中文（如果是中文就翻译成英文），只输出翻译结果：{text}")
                yield event.plain_result(f"翻译结果：\n{result}")
                return

        # ==================== addadmin 交互 ====================
        if cmd == "addadmin":
            if step == "user_id":
                user = get_user(user_id)
                if not user or not check_permission(user, "manage_admin"):
                    clear_state(user_id)
                    yield event.plain_result("权限不足（需要超级管理员）")
                    return
                target = get_user(text)
                if not target:
                    clear_state(user_id)
                    yield event.plain_result("用户不存在")
                    return
                conn = get_db()
                conn.execute("UPDATE users SET role='admin' WHERE tg_id=?", (text,))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"已将 {target['nickname']} 设为管理员")
                return

        # ==================== setsuperadmin 交互 ====================
        if cmd == "setsuperadmin":
            if step == "user_id":
                user = get_user(user_id)
                if not user or user["role"] != "owner":
                    clear_state(user_id)
                    yield event.plain_result("只有机器人创建者可以设置超级管理员")
                    return
                conn = get_db()
                conn.execute("UPDATE users SET role='super_admin' WHERE tg_id=?", (text,))
                conn.commit()
                conn.close()
                target = get_user(text)
                clear_state(user_id)
                yield event.plain_result(f"已将 {target['nickname'] if target else text} 设为超级管理员")
                return

        # ==================== setperm 交互 ====================
        if cmd == "setperm":
            if step == "user_id":
                user = get_user(user_id)
                if not user or user["role"] not in ("super_admin", "owner"):
                    clear_state(user_id)
                    yield event.plain_result("权限不足（需要超级管理员）")
                    return
                target = get_user(text)
                if not target or target["role"] != "admin":
                    clear_state(user_id)
                    yield event.plain_result("目标用户不是管理员")
                    return
                state["data"]["target_id"] = text
                state["step"] = "perm"
                yield event.plain_result("请输入权限名（all=全部权限，manage_admin=管理管理员，manage_ai=管理AI，view_users=查看用户）")
                return
            elif step == "perm":
                target_id = state["data"]["target_id"]
                target = get_user(target_id)
                perms = json.loads(target.get("permissions", "[]"))
                if text not in perms:
                    perms.append(text)
                conn = get_db()
                conn.execute("UPDATE users SET permissions=? WHERE tg_id=?", (json.dumps(perms), target_id))
                conn.commit()
                conn.close()
                clear_state(user_id)
                yield event.plain_result(f"已给 {target['nickname']} 分配权限：{text}\n当前权限：{', '.join(perms)}")
                return

    # ==================== 命令入口（只设置交互状态） ====================
    @filter.command("register")
    async def register(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        if get_user(user_id):
            yield event.plain_result("你已经注册过了，用 /login 登录")
            return
        set_state(user_id, "register", "password")
        yield event.plain_result("请输入密码（至少4位）")

    @filter.command("login")
    async def login(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        if not get_user(user_id):
            yield event.plain_result("你还没注册，用 /register 注册")
            return
        set_state(user_id, "login", "password")
        yield event.plain_result("请输入密码")

    @filter.command("secondpass")
    async def second_pass(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        if not user["second_password_hash"]:
            yield event.plain_result("你没有设置二级密码")
            return
        set_state(user_id, "secondpass", "password")
        yield event.plain_result("请输入二级密码")

    @filter.command("profile")
    async def profile(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        role_text = {"super_admin": "超级管理员", "admin": "管理员", "user": "普通用户", "owner": "创建者"}.get(user["role"], "普通用户")
        reg_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(user["register_time"]))
        yield event.plain_result(
            f"个人信息\n昵称：{user['nickname']}\n身份：{role_text}\n注册时间：{reg_time}\n二级密码：{'已设置' if user['second_password_hash'] else '未设置'}"
        )

    @filter.command("ai")
    async def ai_command(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录（/register 注册，/login 登录）")
            return
        set_state(user_id, "ai", "question")
        yield event.plain_result("请输入你想问的问题")

    @filter.command("setai")
    async def set_ai(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user or not check_permission(user, "manage_ai"):
            yield event.plain_result("权限不足")
            return
        set_state(user_id, "setai", "api_key")
        yield event.plain_result("请输入 API Key")

    @filter.command("upload")
    async def upload_file(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "upload", "tags")
        yield event.plain_result("请输入文件标签（不需要标签直接发 无）")

    @filter.command("files")
    async def list_files(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        conn = get_db()
        files = conn.execute("SELECT * FROM files WHERE user_id=? ORDER BY upload_time DESC LIMIT 20",
            (user_id,)).fetchall()
        conn.close()
        if not files:
            yield event.plain_result("你还没有上传文件\n发 /upload 上传文件")
            return
        text = "我的文件（最近20个）\n"
        for f in files:
            t = time.strftime("%m-%d %H:%M", time.localtime(f["upload_time"]))
            text += f"#{f['id']} {f['file_name']} ({format_size(f['file_size'])}) {t}\n"
        yield event.plain_result(text)

    @filter.command("search")
    async def search_files(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "search", "keyword")
        yield event.plain_result("请输入搜索关键词")

    @filter.command("delfile")
    async def delete_file(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "delfile", "file_id")
        yield event.plain_result("请输入要删除的文件ID（用 /files 查看ID）")

    @filter.command("set")
    async def set_storage(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "set", "key")
        yield event.plain_result("请输入键名")

    @filter.command("get")
    async def get_storage(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "get", "key")
        yield event.plain_result("请输入键名")

    @filter.command("del")
    async def del_storage(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        set_state(user_id, "del", "key")
        yield event.plain_result("请输入要删除的键名")

    @filter.command("keys")
    async def list_keys(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        conn = get_db()
        records = conn.execute("SELECT * FROM storage WHERE user_id=? ORDER BY update_time DESC",
            (user_id,)).fetchall()
        conn.close()
        if not records:
            yield event.plain_result("没有存储数据\n发 /set 存储数据")
            return
        text = "我的存储\n"
        for r in records:
            t = time.strftime("%m-%d %H:%M", time.localtime(r["update_time"]))
            val_preview = r["value"][:30] + "..." if len(r["value"]) > 30 else r["value"]
            text += f"{r['key']} = {val_preview} ({t})\n"
        yield event.plain_result(text)

    @filter.command("weather")
    async def weather(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        set_state(user_id, "weather", "city")
        yield event.plain_result("请输入城市名")

    @filter.command("translate")
    async def translate(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        set_state(user_id, "translate", "text")
        yield event.plain_result("请输入要翻译的文字")

    @filter.command("dice")
    async def dice(self, event: AstrMessageEvent):
        n = 1
        results = [random.randint(1, 6) for _ in range(n)]
        yield event.plain_result(f"掷出 {n} 个骰子：{results}\n总和：{sum(results)}")

    @filter.command("sign")
    async def sign(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先登录")
            return
        today = time.strftime("%Y-%m-%d")
        key = f"sign_{today}"
        conn = get_db()
        existing = conn.execute("SELECT * FROM storage WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        if existing:
            conn.close()
            yield event.plain_result("今天已经签到过了，明天再来吧")
            return
        total = conn.execute("SELECT COUNT(*) as c FROM storage WHERE user_id=? AND key LIKE 'sign_%'",
            (user_id,)).fetchone()["c"] + 1
        now = int(time.time())
        conn.execute("INSERT INTO storage (user_id, key, value, update_time) VALUES (?, ?, ?, ?)",
            (user_id, key, "1", now))
        conn.commit()
        conn.close()
        yield event.plain_result(f"签到成功！\n累计签到：{total} 天\n明天继续哦")

    @filter.command("addadmin")
    async def add_admin(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user or not check_permission(user, "manage_admin"):
            yield event.plain_result("权限不足（需要超级管理员）")
            return
        set_state(user_id, "addadmin", "user_id")
        yield event.plain_result("请输入要设为管理员的用户ID")

    @filter.command("setsuperadmin")
    async def set_super_admin(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user or user["role"] != "owner":
            yield event.plain_result("只有机器人创建者可以设置超级管理员")
            return
        set_state(user_id, "setsuperadmin", "user_id")
        yield event.plain_result("请输入要设为超级管理员的用户ID")

    @filter.command("setperm")
    async def set_perm(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user or user["role"] not in ("super_admin", "owner"):
            yield event.plain_result("权限不足（需要超级管理员）")
            return
        set_state(user_id, "setperm", "user_id")
        yield event.plain_result("请输入要分配权限的管理员用户ID")

    @filter.command("userlist")
    async def user_list(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user or not check_permission(user, "view_users"):
            yield event.plain_result("权限不足")
            return
        conn = get_db()
        users = conn.execute("SELECT * FROM users ORDER BY register_time DESC LIMIT 20").fetchall()
        conn.close()
        text = "用户列表（最近20个）\n"
        for u in users:
            role = {"super_admin": "超管", "admin": "管理", "user": "用户", "owner": "创建者"}.get(u["role"], u["role"])
            text += f"[{role}] {u['nickname']} (ID:{u['tg_id']})\n"
        yield event.plain_result(text)

    @filter.command("initowner")
    async def init_owner(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)
        if not user:
            yield event.plain_result("请先注册账号")
            return
        conn = get_db()
        owner = conn.execute("SELECT * FROM users WHERE role='owner'").fetchone()
        if owner:
            conn.close()
            yield event.plain_result("已经有创建者了")
            return
        conn.execute("UPDATE users SET role='owner' WHERE tg_id=?", (user_id,))
        conn.commit()
        conn.close()
        yield event.plain_result(f"已将 {user['nickname']} 设为机器人创建者（最高权限）")

    # ==================== 帮助命令（分权限） ====================
    @filter.command("help")
    async def help_cmd(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        user = get_user(user_id)

        # 普通用户帮助（所有人可见）
        text = """📖 命令大全

🔐 用户系统
/register - 注册账号
/login - 登录
/secondpass - 验证二级密码
/profile - 个人信息

🤖 AI 聊天
/ai - 和 AI 对话

📁 文件管理
/upload - 上传文件
/files - 我的文件
/search - 搜索文件内容
/delfile - 删除文件

📦 存储功能
/set - 存储数据
/get - 读取数据
/del - 删除数据
/keys - 所有存储键

🛠 实用工具
/weather - 天气查询
/translate - 翻译
/dice - 掷骰子
/sign - 每日签到
"""

        # 管理员及以上才显示管理命令
        if user and is_admin(user):
            text += """
👑 管理命令
/addadmin - 添加管理员
/setsuperadmin - 设超级管理员
/setperm - 分配管理员权限
/userlist - 查看用户列表
/setai - 设置 AI API
/initowner - 初始化创建者（首次）
"""

        yield event.plain_result(text)

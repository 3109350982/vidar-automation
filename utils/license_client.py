import os, json, time, platform, uuid, hashlib, threading, requests

# 服务端地址（可写死，也可用环境变量覆盖）
LICENSE_SERVER = os.environ.get("LICENSE_SERVER", "https://license.cjylkr20241008.top/").rstrip("/")
ACTIVATE_URL = f"{LICENSE_SERVER}/v1/licenses/activate"
VERIFY_URL   = f"{LICENSE_SERVER}/v1/licenses/verify"

CACHE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "license_cache.json"))

_state = {"valid": False, "exp": 0, "token_exp": 0, "lic_exp": 0, "key": "", "token": ""}

def _hwid():
    bits = [platform.system(), platform.release(), platform.machine(), platform.node(), str(uuid.getnode())]
    return hashlib.sha256("||".join(bits).encode()).hexdigest()

def _load():
    if os.path.exists(CACHE_PATH):
        try: return json.load(open(CACHE_PATH,"r",encoding="utf-8"))
        except: pass
    return {}

def _save(d):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    os.replace(tmp, CACHE_PATH)

def status():
    """返回当前许可状态（exp 优先等于 lic_exp；兼容旧前端，同时提供 token_exp/lic_exp）"""
    now = int(time.time())
    d = {**_state}

    token_exp = int(d.get("token_exp") or d.get("exp") or 0)
    lic_exp   = int(d.get("lic_exp") or d.get("license_exp") or 0)
    token_exists = bool(d.get("token"))

    # 判定逻辑（lic_exp 优先）
    lic_expired = (lic_exp > 0 and now >= lic_exp)
    token_expired = (token_exp > 0 and now >= token_exp)

    # 以 lic_exp 为准，若不存在 lic_exp 则退回 token_exp
    if lic_exp > 0:
        valid = token_exists and not lic_expired
        expired = lic_expired
    else:
        valid = token_exists and not token_expired
        expired = token_expired

    # 强制同步状态
    _state["valid"] = valid
    _state["expired"] = expired

    d.update({
        "valid": valid,
        "expired": expired,
        "token_exp": token_exp,
        "lic_exp": lic_exp,
        "exp": lic_exp or token_exp
    })
    return d




def init_from_cache():
    """启动时读取缓存；无论 token 是否有效，都保留上次的 key 用于前端预填"""
    c = _load()
    _state.update({"valid": False, "exp": 0, "token_exp": 0, "lic_exp": 0, "key": "", "token": ""})

    if not c:
        return

    if "key" in c:        _state["key"] = c.get("key") or ""
    if "token" in c:      _state["token"] = c.get("token") or ""
    if "exp" in c:        _state["exp"] = int(c.get("exp") or 0)             # 兼容旧字段
    if "token_exp" in c:  _state["token_exp"] = int(c.get("token_exp") or 0)
    if "lic_exp" in c:    _state["lic_exp"]   = int(c.get("lic_exp") or 0)

    # 统一：token_exp 优先
    if not _state["token_exp"]:
        _state["token_exp"] = int(_state["exp"] or 0)

    now = int(time.time())
    token_ok = bool(_state["token"]) and (_state["token_exp"] > now)
    lic_ok   = (_state["lic_exp"] > now) if _state["lic_exp"] else True

    _state["valid"] = token_ok and lic_ok
    now = int(time.time())
    if _state.get("lic_exp", 0) and _state["lic_exp"] <= now:
        _state["valid"] = False
        _state["expired"] = True


def _pick_license_exp(j: dict) -> int:
    """从返回 JSON 中尽可能取到许可证真实到期时间（秒级 Unix 时间戳）"""
    # 尝试多个常见命名
    for k in ("license_exp", "license_exp_ts", "license_until", "lic_exp", "lic_expire_ts", "expire_at", "expires_at"):
        if k in j and j[k]:
            v = int(j[k])
            # 兼容毫秒
            return v // 1000 if v > 10**12 else v
    return 0

def activate(key: str):
    """激活成功后写入完整缓存（key/token/token_exp/lic_exp），供下次启动预填"""
    r = requests.post(ACTIVATE_URL, json={
        "key": key, "hwid": _hwid(), "product": "xhs-auto", "ttl_hours": 1
    }, timeout=15)
    j = r.json()
    if j.get("status") != "ok":
        print(f"[LIC-DEBUG] activate failed resp={j}", flush=True)
        raise RuntimeError(j.get("message", "activate_failed"))

    token = j["token"]
    token_exp = int(j.get("exp") or 0)
    if token_exp > 10**12:  # 兼容毫秒
        token_exp //= 1000

    lic_exp = _pick_license_exp(j)
    if lic_exp > 0:
        token_exp = lic_exp+3600
    # 若激活返回没有 lic_exp，尝试 verify 拿
    if lic_exp <= 0:
        try:
            r2 = requests.post(VERIFY_URL, json={"token": token, "hwid": _hwid()}, timeout=15)
            j2 = r2.json() if r2 is not None else {}
            if j2.get("status") == "ok":
                lic_exp = _pick_license_exp(j2) or lic_exp
            
        except:
            pass
    now = int(time.time())
    if lic_exp and lic_exp <= now:
        _state["valid"] = False
        _state["expired"] = True
    else:
        _state["valid"] = True
        _state["expired"] = False
    _state.update({
        # "valid": True,
        "key": key,
        "token": token,
        "token_exp": token_exp,
        "lic_exp": lic_exp,
        "exp": token_exp  # 兼容旧字段，不再依赖它做判断
    })

    _save({"key": key, "token": token, "token_exp": token_exp, "lic_exp": lic_exp, "exp": token_exp})
    return status()

def start_recheck(interval_seconds=3600, exit_on_invalid=True):
    """后台巡检：默认每 60 秒一次；以 lic_exp 为准，过期立刻退出"""
    def _loop():
        while True:
            try:
                s = status()
                now = int(time.time())

                # 1) 本地立即判定（只要有 lic_exp，就以 lic_exp 为准）
                lic_exp = int(s.get("lic_exp") or 0)
                if lic_exp > 0 and lic_exp <= now:
                    _state["valid"] = False
                    _state["expired"] = True
                    time.sleep(max(5, int(interval_seconds)))
                    continue

                # 2) 本地未过期时，尝试向服务器 verify 一次
                token = _state.get("token", "")
                if token:
                    try:
                        r = requests.post(VERIFY_URL, json={"token": token, "hwid": _hwid()}, timeout=10)
                        j = r.json()
                        if j.get("status") != "ok":
                            _state["valid"] = False
                            _state["expired"] = True
                        else:
                            # 同步服务器返回的真实 lic_exp（若有）
                            le = _pick_license_exp(j)
                            if le > 0 and le != _state.get("lic_exp", 0):
                                _state["lic_exp"] = le
                                _save({
                                    "key": _state.get("key",""),
                                    "token": token,
                                    "token_exp": int(_state.get("token_exp") or _state.get("exp") or 0),
                                    "lic_exp": le,
                                    "exp": int(_state.get("token_exp") or _state.get("exp") or 0)
                                })
                    except Exception:
                        # 网络异常忽略，等下一轮
                        pass

                time.sleep(max(5, int(interval_seconds)))
            except Exception:
                time.sleep(max(5, int(interval_seconds)))

    t = threading.Thread(target=_loop, daemon=True)
    t.start()



def clear_cache():
    """清除本地缓存的 key/token/exp，并重置内存状态"""
    try:
        if os.path.exists(CACHE_PATH):
            os.remove(CACHE_PATH)
    except:
        pass
    _state.update({"valid": False, "exp": 0, "key": "", "token": ""})
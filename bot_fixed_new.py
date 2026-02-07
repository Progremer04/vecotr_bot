import datetime
import json
import zipfile
import os
import tempfile
import shutil
import sys
import subprocess
import asyncio
import logging
import random
import time
import uuid
from collections import defaultdict
from pathlib import Path
import html
import csv
import io

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ForceReply)
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TimedOut, BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

# ------------------ CONFIGURATION ------------------
BOT_TOKEN = '8595445360:AAGDS1yg-jFEAyQUUfsWZo27WGZ-dgFcn7I'
PRIMARY_ADMIN_USERNAME = 'allicheamine2'

# ------------------ PATHS ------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
ACCOUNTS_DIR = BASE_DIR / 'accounts_data'
COOKIES_DIR = BASE_DIR / 'cookies_data'
TEMP_DIR = BASE_DIR / 'temp_data'
BACKGROUND_IMAGE_PATH_CONFIG_KEY = 'background_image_path'
DEFAULT_BACKGROUND_IMAGE_FILENAME = 'backround.png'

# Data files
USERS_JSON_FILE = DATA_DIR / 'users.json'
USER_POINTS_FILE = DATA_DIR / 'user_points.csv'
ADMINS_FILE = DATA_DIR / 'admins.json'
BANNED_FILE = DATA_DIR / 'banned.json'
CONFIG_FILE = DATA_DIR / 'config.json'
SERVICES_FILE = DATA_DIR / 'services.json'
SERVICE_PRICES_FILE = DATA_DIR / 'service_prices.json'
SERVICE_ITEMS_FILE = DATA_DIR / 'service_items.json'
KEYS_FILE = DATA_DIR / 'keys.json'
POINT_KEYS_FILE = DATA_DIR / 'point_keys.json'
REFERRALS_FILE = DATA_DIR / 'referrals.csv'
FEEDBACKS_FILE = DATA_DIR / 'feedbacks.json'
TRANSFERS_FILE = DATA_DIR / 'transfers.json'
CLAIMED_ACCOUNTS_FILE = DATA_DIR / 'claimed_accounts.json'
PERMANENT_BANS_FILE = DATA_DIR / 'permanent_bans.json'
BAN_DATA_FILE = DATA_DIR / 'ban_data.json'
BOT_STATUS_FILE = DATA_DIR / 'bot_status.json'

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ------------------ LOGGING ------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------ GLOBALS ------------------
bot_config = {}
admin_user_ids = set()
banned_users = {}
permanent_bans = set()
user_requests = defaultdict(list)
services = {}
feedback_map = {}
bot_enabled = True

# Conversation states
WAITING_FOR_FEEDBACK = 1
AWAITING_ADMIN_INPUT = 2
WAITING_FOR_KEY = 3
TRANSFER_POINTS = 10
SEARCH_USER = 20

DEFAULT_BOT_CONFIG = {
    "channels": [
        {"username": "@shadowvaultbotshadow", "chat_id": -1003144366488},
    ],
    "primary_admin_contact_username": PRIMARY_ADMIN_USERNAME,
    BACKGROUND_IMAGE_PATH_CONFIG_KEY: DEFAULT_BACKGROUND_IMAGE_FILENAME,
    "points_per_referral": 100,
    "points_per_account_bonus": 50,
    "default_points_on_join": 1,
    "key_validity_days": {"standard": 30, "premium": 90, "lifetime": 3650},
    "ban_durations_seconds": {"normal_user": 86399, "premium_user": 86399},
    "request_limits_per_10_min": {"normal_user": 5, "premium_user": 20},
    "low_stock_threshold": 5,
    "bot_enabled": True
    ,
    # Maximum hours a user must wait before claiming another account from the same service.
    # Admins can adjust this via the settings menu.
    "claim_limit_hours": 24,
    # Request window in seconds used for rate limiting (default 10 minutes = 600 seconds).
    # Controls the time period over which the request limit applies.
    "request_window_seconds": 600,
    # Optional per‑service claim limit overrides (in hours). Empty dict by default.
    "service_claim_limit_hours": {},
    # Log channel for claim notifications (optional). Set via admin settings.
    "log_channel": None,
    # Claim notification settings: "admins", "log_channel", "both", or "none"
    "claim_notification_mode": "admins",
    # Temporary ban duration in seconds (separate from permanent bans)
    "temp_ban_duration_seconds": {"normal_user": 86399, "premium_user": 86399},
    # Maximum claims per account per day for standard and premium users
    "max_claims_per_day": {"standard": 5, "premium": 20}
}

# ------------------ FILE-BASED DATA MANAGEMENT ------------------

def initialize_data_files():
    """Initialize all data files with defaults if they don't exist"""
    # Config
    if not CONFIG_FILE.exists():
        save_json(CONFIG_FILE, DEFAULT_BOT_CONFIG)
    else:
        existing_config = load_json(CONFIG_FILE)
        merged_config = DEFAULT_BOT_CONFIG.copy()
        for key, value in existing_config.items():
            if key in merged_config and isinstance(value, dict) and isinstance(merged_config[key], dict):
                merged_config[key].update(value)
            else:
                merged_config[key] = value
        save_json(CONFIG_FILE, merged_config)

    # Other files
    files_to_init = [
        (ADMINS_FILE, []),
        (BANNED_FILE, {}),
        (PERMANENT_BANS_FILE, []),
        (USERS_JSON_FILE, {}),
        (SERVICES_FILE, {}),
        (SERVICE_PRICES_FILE, {}),
        (SERVICE_ITEMS_FILE, {}),
        (KEYS_FILE, {}),
        (POINT_KEYS_FILE, {}),
        (FEEDBACKS_FILE, {}),
        (TRANSFERS_FILE, {}),
        (CLAIMED_ACCOUNTS_FILE, {}),
        (BOT_STATUS_FILE, {"enabled": True})
    ]

    for file_path, default_data in files_to_init:
        if not file_path.exists():
            save_json(file_path, default_data)

    # CSV files
    if not USER_POINTS_FILE.exists():
        with open(USER_POINTS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['user_id', 'username', 'points', 'last_updated'])

    if not REFERRALS_FILE.exists():
        with open(REFERRALS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['referral_id', 'referrer_id', 'referee_id', 'referral_date', 'status'])

def save_json(filepath, data):
    """Save data to JSON file"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_json(filepath):
    """Load data from JSON file"""
    if not filepath.exists():
        return {} if 'json' in filepath.name else []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {} if 'json' in filepath.name else []

def read_csv(filepath):
    """Read CSV file and return as list of dicts"""
    if not filepath.exists():
        return []
    data = []
    with open(filepath, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    return data

def write_csv(filepath, data, fieldnames):
    """Write data to CSV file"""
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

def append_csv_row(filepath, row_dict, fieldnames):
    """Append a row to CSV file"""
    file_exists = filepath.exists()
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_dict)

# ------------------ HELPER FUNCTIONS ------------------
def get_safe_background():
    default_bg_filename = bot_config.get(BACKGROUND_IMAGE_PATH_CONFIG_KEY, DEFAULT_BACKGROUND_IMAGE_FILENAME)
    default_bg_path = default_bg_filename
    try:
        if Path(default_bg_path).is_file():
            if os.path.getsize(default_bg_path) < 5 * 1024 * 1024:
                return default_bg_path
            else:
                logger.warning(f"Default background image {default_bg_path} is too large (>5MB), skipping.")
        else:
            logger.warning(f"Default background image {default_bg_path} not found.")
        placeholder_path = BASE_DIR / 'placeholder_light_gray_block.png'
        if placeholder_path.is_file():
            logger.info(f"Using fallback placeholder background at {placeholder_path}.")
            return str(placeholder_path)
        return None
    except Exception as e:
        logger.error(f"Error getting safe background: {e}")
        return None

def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = text.replace('\\', '\\\\')
    for char in r'_*[]()~`>#+-=|{}.!':
        text = text.replace(char, f'\\{char}')
    return text

def clean_markdown_text(text):
    if not text:
        return ""
    import re
    markdown_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in markdown_chars:
        text = text.replace(char, f'\\{char}')
    text = re.sub(r'^(#+\s)', r'\\1', text, flags=re.MULTILINE)
    text = re.sub(r'^(\d+\.\s)', r'\\1', text, flags=re.MULTILINE)
    text = re.sub(r'^([-*]\s)', r'\\1', text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\\)(https?://\S+)', r'<\\1>', text)
    return text

def truncate_text(text, max_length=4096):
    if len(text) <= max_length:
        return text
    truncated = text[:max_length-3]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')
    if last_period > max_length * 0.8:
        return truncated[:last_period+1] + '..'
    elif last_newline > max_length * 0.8:
        return truncated[:last_newline] + '...'
    else:
        return truncated + '...'

async def safe_send_message(chat_id, text, context: ContextTypes.DEFAULT_TYPE, parse_mode=ParseMode.MARKDOWN, reply_markup=None, retries=3, delay=1, disable_web_page_preview=None):
    if not text or not text.strip():
        logger.warning("Attempted to send empty message")
        return None
    if disable_web_page_preview is None:
        disable_web_page_preview = True
    original_text = text.strip()
    current_text = original_text
    current_parse_mode = parse_mode
    current_reply_markup = reply_markup
    current_disable_preview = disable_web_page_preview
    for attempt in range(retries):
        try:
            return await context.bot.send_message(
                chat_id=chat_id,
                text=current_text,
                parse_mode=current_parse_mode,
                reply_markup=current_reply_markup,
                disable_web_page_preview=current_disable_preview
            )
        except BadRequest as e:
            error_msg = str(e).lower()
            logger.error(f"BadRequest sending message (attempt {attempt+1}): '{str(e)}'. Text: '{current_text[:200]}'. ParseMode: {current_parse_mode}")
            if "can't parse entities" in error_msg or "parse error" in error_msg:
                if current_parse_mode == ParseMode.MARKDOWN:
                    logger.info("Markdown parsing failed, trying plain text")
                    current_parse_mode = None
                    continue
                else:
                    logger.info("Plain text also failed, cleaning text and retrying")
                    current_text = clean_markdown_text(original_text)
                    current_parse_mode = None
                    continue
            elif "message is too long" in error_msg:
                logger.info("Message too long, truncating text")
                current_text = truncate_text(current_text, 2000)
                continue
            elif "reply markup is too long" in error_msg:
                logger.warning("Reply markup too long, removing it")
                current_reply_markup = None
                continue
            elif "chat not found" in error_msg or "bot was blocked" in error_msg:
                logger.error(f"Non-recoverable error: {error_msg}")
                raise
            else:
                logger.info("Unknown BadRequest, trying plain text")
                current_parse_mode = None
                continue
        except (NetworkError, TimedOut) as e:
            logger.warning(f"Send message attempt {attempt+1} failed (Network/Timeout). Retrying in {delay * (2**attempt)}s: {e}")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))
        except RetryAfter as e:
            wait_time = e.retry_after + 0.5
            logger.warning(f"Send message attempt {attempt+1} failed (RetryAfter {e.retry_after}s). Retrying in {wait_time}s.")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Unexpected error sending message (attempt {attempt+1}): {e}", exc_info=True)
            if attempt == retries - 1:
                try:
                    minimal_text = "📨 Message\n\n" + original_text[:50] + "..." if len(original_text) > 50 else original_text
                    return await context.bot.send_message(
                        chat_id=chat_id,
                        text=minimal_text,
                        parse_mode=None,
                        reply_markup=None,
                        disable_web_page_preview=True
                    )
                except Exception as last_e:
                    logger.critical(f"Complete failure to send message: {last_e}")
                    raise
            await asyncio.sleep(delay * (2 ** attempt))
    return None

async def safe_send_document(chat_id, document, context: ContextTypes.DEFAULT_TYPE, filename=None, caption=None, parse_mode=ParseMode.MARKDOWN, reply_markup=None, retries=3, delay=1):
    for attempt in range(retries):
        if hasattr(document, 'seek') and attempt > 0:
            document.seek(0)
        try:
            return await context.bot.send_document(
                chat_id=chat_id, document=document, filename=filename,
                caption=caption, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as e:
            logger.error(f"BadRequest sending document (attempt {attempt+1}): '{str(e)}'. Caption: '{caption[:100] if caption else None}'. ParseMode: {parse_mode}")
            if caption and parse_mode == ParseMode.MARKDOWN and "can't parse entities" in str(e).lower():
                logger.info("Retrying document with plain text caption.")
                try:
                    return await context.bot.send_document(chat_id=chat_id, document=document, filename=filename, caption=caption, reply_markup=reply_markup)
                except Exception as plain_e:
                    logger.error(f"Failed to send document with plain text caption: {plain_e}")
            if attempt == retries - 1:
                raise
        except (NetworkError, TimedOut) as e:
            logger.warning(f"Send document attempt {attempt+1} failed (Network/Timeout). Retrying in {delay * (2**attempt)}s: {e}")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))
        except RetryAfter as e:
            logger.warning(f"Send document attempt {attempt+1} failed (RetryAfter {e.retry_after}s). Retrying.")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
        except Exception as e:
            logger.error(f"Unexpected error sending document (attempt {attempt+1}): {e}", exc_info=True)
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))

async def safe_send_photo(chat_id, photo, context: ContextTypes.DEFAULT_TYPE, caption=None, parse_mode=ParseMode.MARKDOWN, reply_markup=None, retries=3, delay=1):
    for attempt in range(retries):
        if hasattr(photo, 'seek') and attempt > 0:
            try:
                photo.seek(0)
            except Exception as e_seek:
                logger.warning(f"Could not seek photo object: {e_seek}")
        try:
            return await context.bot.send_photo(
                chat_id=chat_id, photo=photo, caption=caption,
                parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as e:
            logger.error(f"BadRequest sending photo (attempt {attempt+1}): '{str(e)}'. Caption: '{caption[:100] if caption else None}'. ParseMode: {parse_mode}")
            if caption and parse_mode == ParseMode.MARKDOWN and "can't parse entities" in str(e).lower():
                logger.info("Retrying photo with plain text caption.")
                try:
                    return await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, reply_markup=reply_markup)
                except Exception as plain_e:
                    logger.error(f"Failed to send photo with plain text caption: {plain_e}")
            if attempt == retries - 1:
                raise
        except (NetworkError, TimedOut) as e:
            logger.warning(f"Send photo attempt {attempt+1} failed (Network/Timeout). Retrying in {delay * (2**attempt)}s: {e}")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))
        except RetryAfter as e:
            logger.warning(f"Send photo attempt {attempt+1} failed (RetryAfter {e.retry_after}s). Retrying.")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
        except Exception as e:
            logger.error(f"Unexpected error sending photo (attempt {attempt+1}): {e}", exc_info=True)
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))

# ------------------ DATA MANAGEMENT ------------------
def load_config():
    global bot_config, bot_enabled
    bot_config = load_json(CONFIG_FILE)
    if not bot_config:
        bot_config = DEFAULT_BOT_CONFIG.copy()
        save_config()
    for key, value in DEFAULT_BOT_CONFIG.items():
        if key not in bot_config:
            bot_config[key] = value
        elif isinstance(value, dict) and isinstance(bot_config[key], dict):
            for sub_key, sub_value in value.items():
                if sub_key not in bot_config[key]:
                    bot_config[key][sub_key] = sub_value
    bot_status_data = load_json(BOT_STATUS_FILE)
    bot_enabled = bot_status_data.get("enabled", True)
    logger.info(f"Bot configuration loaded. Bot enabled: {bot_enabled}")

def save_config():
    save_json(CONFIG_FILE, bot_config)
    logger.info("Config saved to file.")

def load_admins():
    global admin_user_ids
    admin_data = load_json(ADMINS_FILE)
    if isinstance(admin_data, list):
        admin_user_ids = set(admin_data)
    elif isinstance(admin_data, dict):
        admin_user_ids = set(int(k) for k, v in admin_data.items() if v)
    else:
        admin_user_ids = set()
    logger.info(f"Admins loaded: {admin_user_ids}")

def save_admins():
    save_json(ADMINS_FILE, list(admin_user_ids))
    logger.info("Admins saved.")

def load_banned_users():
    global banned_users, permanent_bans
    banned_users = load_json(BANNED_FILE)

    permanent_bans_data = load_json(PERMANENT_BANS_FILE)
    if isinstance(permanent_bans_data, list):
        permanent_bans = set(permanent_bans_data)
    logger.info(f"Banned users loaded. Permanent bans: {len(permanent_bans)}")

    current_time = time.time()
    expired_bans = [user for user, expiry_details in banned_users.items()
                    if isinstance(expiry_details, (int, float)) and current_time >= expiry_details
                    and str(user) not in permanent_bans]
    if expired_bans:
        for user_to_unban in expired_bans:
            del banned_users[user_to_unban]
            logger.info(f"Expired ban removed for user: {user_to_unban}")
        save_banned_users()

def save_banned_users():
    save_json(BANNED_FILE, banned_users)
    save_json(PERMANENT_BANS_FILE, list(permanent_bans))
    logger.info("Banned users list saved")

def is_admin(user_id_to_check: int) -> bool:
    if not user_id_to_check:
        return False
    return int(user_id_to_check) in admin_user_ids

def is_user_banned(username: str) -> int | bool:
    if username is None:
        return False

    if str(username) in permanent_bans:
        return 9999999999

    if username in banned_users:
        expiry_timestamp = banned_users[username]
        if not isinstance(expiry_timestamp, (int, float)):
            logger.warning(f"Invalid expiry timestamp for {username}: {expiry_timestamp}. Removing ban.")
            del banned_users[username]
            save_banned_users()
            return False
        current_time = time.time()
        if current_time < expiry_timestamp:
            seconds_left = int(expiry_timestamp - current_time)
            return seconds_left
        else:
            logger.info(f"Ban expired for user {username}. Removing from list.")
            del banned_users[username]
            save_banned_users()
            return False
    return False

def is_user_permanently_banned(user_id: str) -> bool:
    return str(user_id) in permanent_bans

def permanent_ban_user(user_id: str) -> bool:
    try:
        permanent_bans.add(str(user_id))
        save_banned_users()
        logger.info(f"User {user_id} permanently banned")
        return True
    except Exception as e:
        logger.error(f"Error permanently banning user {user_id}: {e}")
        return False

def remove_permanent_ban(user_id: str) -> bool:
    try:
        user_id_str = str(user_id)
        if user_id_str in permanent_bans:
            permanent_bans.remove(user_id_str)
            save_banned_users()
            logger.info(f"Permanent ban removed for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error removing permanent ban for user {user_id}: {e}")
        return False

def check_and_ban_user(username: str, user_id_for_premium_check: int = None, unban: bool = False, manual: bool = False) -> bool:
    # If we have a user_id, check if they are admin
    if user_id_for_premium_check and is_admin(user_id_for_premium_check):
        logger.info(f"User {username} (ID: {user_id_for_premium_check}) is an admin, skipping ban check.")
        return False
    
    # If we only have username, try to find user_id to check admin status
    if not user_id_for_premium_check and username:
        users_data = load_json(USERS_JSON_FILE)
        for uid, data in users_data.items():
            if data.get('username') == username:
                if is_admin(int(uid)):
                    logger.info(f"User {username} (ID: {uid}) is an admin, skipping ban check.")
                    return False
                break
    if unban:
        if username in banned_users:
            del banned_users[username]
            save_banned_users()
            logger.info(f"User {username} unbanned by admin action or expiry.")
            return True
        logger.info(f"User {username} was not in banned list for unbanning.")
        return False

    is_premium = False
    if user_id_for_premium_check:
        try:
            is_premium, _ = check_user_premium_status(user_id_for_premium_check)
        except Exception as e:
            logger.error(f"Error checking premium status for user {username} (ID: {user_id_for_premium_check}): {e}")
    else:
        logger.warning(f"No user_id provided for {username} in check_and_ban_user. Assuming non-premium for rate limits.")

    # Prime users are exempt from cooldown/rate limiting
    if is_premium:
        return False

    current_time = time.time()
    # Use configurable request window for rate limiting (default 600 seconds = 10 minutes)
    request_window = bot_config.get("request_window_seconds", DEFAULT_BOT_CONFIG.get("request_window_seconds", 600))
    user_requests[username] = [t for t in user_requests[username] if current_time - t <= request_window]

    ban_durations_conf = bot_config.get("ban_durations_seconds", DEFAULT_BOT_CONFIG["ban_durations_seconds"])
    request_limits_conf = bot_config.get("request_limits_per_10_min", DEFAULT_BOT_CONFIG["request_limits_per_10_min"])

    request_limit = request_limits_conf.get("premium_user") if is_premium else request_limits_conf.get("normal_user")
    ban_duration = ban_durations_conf.get("premium_user") if is_premium else ban_durations_conf.get("normal_user")

    if request_limit is None:
        request_limit = 20 if is_premium else 5
    if ban_duration is None:
        ban_duration = 30 if is_premium else 600

    if manual or len(user_requests[username]) >= request_limit:
        banned_users[username] = current_time + ban_duration
        save_banned_users()
        if manual:
            logger.info(f"User {username} manually banned for {ban_duration}s.")
        else:
            logger.info(f"User {username} (Premium: {is_premium}) banned for {ban_duration}s. Requests: {len(user_requests[username])}/{request_limit}.")
        if username in user_requests:
            del user_requests[username]
        return True

# ==================== NEW FUNCTIONS FOR BAN & CLAIM MANAGEMENT ====================

def unban_temporary_ban_member(username: str) -> tuple[bool, str]:
    """
    Unban a temporarily banned member.
    
    Args:
        username (str): The username of the member to unban
        
    Returns:
        tuple[bool, str]: (success, message)
            - (True, message) if unban was successful
            - (False, message) if unban failed or user was not banned
    """
    try:
        if not username:
            return False, "Username cannot be empty."
        
        # Check if user is in temporary bans
        if username not in banned_users:
            return False, f"User '{username}' is not in the temporary ban list."
        
        # Check if it's a permanent ban
        if str(username) in permanent_bans:
            return False, f"User '{username}' is permanently banned. Use remove_permanent_ban() instead."
        
        # Remove from temporary bans
        del banned_users[username]
        save_banned_users()
        
        logger.info(f"User {username} has been unbanned from temporary ban list.")
        return True, f"✅ User '{username}' has been successfully unbanned."
        
    except Exception as e:
        logger.error(f"Error unbanning user {username}: {e}")
        return False, f"❌ Error unbanning user: {str(e)}"

def set_temporary_ban_time(ban_duration_seconds: int, user_type: str = "normal_user") -> tuple[bool, str]:
    """
    Set the duration time for temporary bans for a specific user type.
    
    Args:
        ban_duration_seconds (int): Duration of ban in seconds
        user_type (str): Type of user - "normal_user" or "premium_user"
        
    Returns:
        tuple[bool, str]: (success, message)
            - (True, message) if update was successful
            - (False, message) if update failed
    """
    try:
        if user_type not in ["normal_user", "premium_user"]:
            return False, f"❌ Invalid user type '{user_type}'. Must be 'normal_user' or 'premium_user'."
        
        if ban_duration_seconds < 0:
            return False, "❌ Ban duration cannot be negative."
        
        if ban_duration_seconds == 0:
            return False, "❌ Ban duration must be greater than 0 seconds."
        
        # Update the global config
        global bot_config
        
        if "ban_durations_seconds" not in bot_config:
            bot_config["ban_durations_seconds"] = DEFAULT_BOT_CONFIG["ban_durations_seconds"].copy()
        
        old_duration = bot_config["ban_durations_seconds"].get(user_type, 0)
        bot_config["ban_durations_seconds"][user_type] = ban_duration_seconds
        
        # Save the updated config
        save_config()
        
        # Convert seconds to readable format
        minutes = ban_duration_seconds // 60
        seconds = ban_duration_seconds % 60
        time_format = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        logger.info(f"Ban duration for {user_type} updated from {old_duration}s to {ban_duration_seconds}s ({time_format})")
        return True, f"✅ Ban duration for '{user_type}' has been set to {time_format} ({ban_duration_seconds} seconds)."
        
    except Exception as e:
        logger.error(f"Error setting ban duration: {e}")
        return False, f"❌ Error setting ban duration: {str(e)}"

def set_claim_limit_per_account(user_type: str, limit_hours: int) -> tuple[bool, str]:
    """
    Set the claim limit (cooldown) for standard and premium members per account/service.
    
    Args:
        user_type (str): Type of user - "standard" or "premium"
        limit_hours (int): Number of hours before a user can claim another account from the same service
        
    Returns:
        tuple[bool, str]: (success, message)
            - (True, message) if update was successful
            - (False, message) if update failed
    """
    try:
        if user_type not in ["standard", "premium"]:
            return False, f"❌ Invalid user type '{user_type}'. Must be 'standard' or 'premium'."
        
        if limit_hours < 0:
            return False, "❌ Claim limit cannot be negative."
        
        if limit_hours == 0:
            return False, "❌ Claim limit must be greater than 0 hours."
        
        # Update the global config
        global bot_config
        
        # For standard users, update the global claim_limit_hours
        # For premium users, we can create a separate setting or override
        if user_type == "standard":
            old_limit = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
            bot_config["claim_limit_hours"] = limit_hours
            config_key = "claim_limit_hours (global)"
        else:  # premium
            if "premium_claim_limit_hours" not in bot_config:
                bot_config["premium_claim_limit_hours"] = DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24)
            old_limit = bot_config.get("premium_claim_limit_hours", 24)
            bot_config["premium_claim_limit_hours"] = limit_hours
            config_key = "premium_claim_limit_hours"
        
        # Save the updated config
        save_config()
        
        logger.info(f"Claim limit for {user_type} users updated from {old_limit}h to {limit_hours}h")
        return True, f"✅ Claim limit for '{user_type}' members has been set to {limit_hours} hour(s)."
        
    except Exception as e:
        logger.error(f"Error setting claim limit: {e}")
        return False, f"❌ Error setting claim limit: {str(e)}"

def get_temporary_ban_info(username: str) -> dict:
    """
    Get information about a user's temporary ban status.
    
    Args:
        username (str): The username to check
        
    Returns:
        dict: Dictionary containing ban information
    """
    try:
        if not username:
            return {"status": "error", "message": "Username cannot be empty."}
        
        # Check if permanently banned
        if str(username) in permanent_bans:
            return {
                "status": "permanently_banned",
                "username": username,
                "ban_type": "permanent",
                "message": f"User '{username}' is permanently banned."
            }
        
        # Check if temporarily banned
        if username in banned_users:
            expiry_timestamp = banned_users[username]
            current_time = time.time()
            
            if isinstance(expiry_timestamp, (int, float)):
                seconds_left = int(expiry_timestamp - current_time)
                if seconds_left > 0:
                    minutes_left = seconds_left // 60
                    seconds_remaining = seconds_left % 60
                    return {
                        "status": "temporarily_banned",
                        "username": username,
                        "ban_type": "temporary",
                        "seconds_left": seconds_left,
                        "minutes_left": minutes_left,
                        "seconds_remaining": seconds_remaining,
                        "expiry_timestamp": expiry_timestamp,
                        "message": f"User '{username}' is banned for {minutes_left}m {seconds_remaining}s."
                    }
                else:
                    return {
                        "status": "ban_expired",
                        "username": username,
                        "ban_type": "temporary",
                        "message": f"User '{username}' ban has expired."
                    }
        
        # Not banned
        return {
            "status": "not_banned",
            "username": username,
            "message": f"User '{username}' is not banned."
        }
        
    except Exception as e:
        logger.error(f"Error getting ban info for {username}: {e}")
        return {"status": "error", "message": f"Error retrieving ban info: {str(e)}"}

def get_claim_limit_info(user_type: str = None) -> dict:
    """
    Get current claim limit settings.
    
    Args:
        user_type (str): Optional - "standard" or "premium" to get specific limit
        
    Returns:
        dict: Dictionary containing claim limit information
    """
    try:
        global bot_config
        
        standard_limit = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
        premium_limit = bot_config.get("premium_claim_limit_hours", standard_limit)
        
        if user_type == "standard":
            return {
                "user_type": "standard",
                "claim_limit_hours": standard_limit,
                "message": f"Standard members must wait {standard_limit} hour(s) between claims."
            }
        elif user_type == "premium":
            return {
                "user_type": "premium",
                "claim_limit_hours": premium_limit,
                "message": f"Premium members must wait {premium_limit} hour(s) between claims."
            }
        else:
            return {
                "standard": {
                    "claim_limit_hours": standard_limit,
                    "message": f"Standard members must wait {standard_limit} hour(s) between claims."
                },
                "premium": {
                    "claim_limit_hours": premium_limit,
                    "message": f"Premium members must wait {premium_limit} hour(s) between claims."
                }
            }
        
    except Exception as e:
        logger.error(f"Error getting claim limit info: {e}")
        return {"status": "error", "message": f"Error retrieving claim limit info: {str(e)}"}


# ==================== CLAIM COUNT & COOLDOWN MANAGEMENT ====================

def set_max_claims_per_user(user_type: str, max_claims: int) -> tuple[bool, str]:
    """
    Set the maximum number of accounts a user can claim per cooldown period.
    
    Args:
        user_type (str): Type of user - "free" or "paid"
        max_claims (int): Maximum number of accounts user can claim (must be > 0)
        
    Returns:
        tuple[bool, str]: (success, message)
            - (True, message) if update was successful
            - (False, message) if update failed
    """
    try:
        if user_type not in ["free", "paid"]:
            return False, f"❌ Invalid user type '{user_type}'. Must be 'free' or 'paid'."
        
        if max_claims < 0:
            return False, "❌ Maximum claims cannot be negative."
        
        if max_claims == 0:
            return False, "❌ Maximum claims must be greater than 0."
        
        # Update the global config
        global bot_config
        
        if "max_claims_per_user" not in bot_config:
            bot_config["max_claims_per_user"] = {}
        
        old_limit = bot_config["max_claims_per_user"].get(user_type, 0)
        bot_config["max_claims_per_user"][user_type] = max_claims
        
        # Save the updated config
        save_config()
        
        logger.info(f"Max claims for {user_type} users updated from {old_limit} to {max_claims}")
        return True, f"✅ Maximum claims for '{user_type}' users has been set to {max_claims} account(s) per cooldown period."
        
    except Exception as e:
        logger.error(f"Error setting max claims: {e}")
        return False, f"❌ Error setting max claims: {str(e)}"

def get_max_claims_per_user(user_type: str = None) -> dict:
    """
    Get the maximum number of claims allowed for user types.
    
    Args:
        user_type (str, optional): "free" or "paid" to get specific limit, None for both
        
    Returns:
        dict: Dictionary containing max claims information
    """
    try:
        global bot_config
        
        free_claims = bot_config.get("max_claims_per_user", {}).get("free", 1)
        paid_claims = bot_config.get("max_claims_per_user", {}).get("paid", 2)
        
        if user_type == "free":
            return {
                "user_type": "free",
                "max_claims": free_claims,
                "message": f"Free users can claim {free_claims} account(s) per cooldown period."
            }
        elif user_type == "paid":
            return {
                "user_type": "paid",
                "max_claims": paid_claims,
                "message": f"Paid users can claim {paid_claims} account(s) per cooldown period."
            }
        else:
            return {
                "free": {
                    "max_claims": free_claims,
                    "message": f"Free users can claim {free_claims} account(s) per cooldown period."
                },
                "paid": {
                    "max_claims": paid_claims,
                    "message": f"Paid users can claim {paid_claims} account(s) per cooldown period."
                }
            }
        
    except Exception as e:
        logger.error(f"Error getting max claims: {e}")
        return {"status": "error", "message": f"Error retrieving max claims: {str(e)}"}

def get_user_claim_count(user_id: str, service_key: str = None) -> dict:
    """
    Get the number of claims a user has made in the current cooldown period.
    
    Args:
        user_id (str): The user ID to check
        service_key (str, optional): Specific service to check, None for all services
        
    Returns:
        dict: Dictionary containing claim count information
    """
    try:
        user_id_str = str(user_id)
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        user_claims = claims_data.get(user_id_str, [])
        
        if not user_claims:
            return {
                "user_id": user_id_str,
                "total_claims": 0,
                "claims_in_period": 0,
                "service_key": service_key,
                "message": f"User {user_id_str} has made 0 claims."
            }
        
        # Get current time and claim limit hours
        now = datetime.datetime.now()
        claim_limit_hours = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
        period_start = now - datetime.timedelta(hours=claim_limit_hours)
        
        # Count claims in current period
        claims_in_period = 0
        for claim in user_claims:
            claim_date_str = claim.get('claim_date') or claim.get('timestamp')
            if claim_date_str:
                try:
                    claim_time = datetime.datetime.fromisoformat(claim_date_str)
                except Exception:
                    try:
                        claim_time = datetime.datetime.strptime(claim_date_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        continue
                
                # Check if claim is in current period
                if claim_time >= period_start:
                    if service_key is None or claim.get('service_key') == service_key:
                        claims_in_period += 1
        
        return {
            "user_id": user_id_str,
            "total_claims": len(user_claims),
            "claims_in_period": claims_in_period,
            "period_hours": claim_limit_hours,
            "service_key": service_key,
            "message": f"User {user_id_str} has made {claims_in_period} claim(s) in the last {claim_limit_hours} hour(s)."
        }
        
    except Exception as e:
        logger.error(f"Error getting user claim count: {e}")
        return {"status": "error", "message": f"Error retrieving claim count: {str(e)}"}

def can_user_make_claim(user_id: str, is_paid: bool = False) -> tuple[bool, str]:
    """
    Check if a user can make another claim based on their claim count limit.
    
    Args:
        user_id (str): The user ID to check
        is_paid (bool): Whether the user is paid (True) or free (False)
        
    Returns:
        tuple[bool, str]: (can_claim, message)
            - (True, message) if user can make a claim
            - (False, message) if user has reached their limit
    """
    try:
        user_type = "paid" if is_paid else "free"
        
        # Get max claims for this user type
        max_claims_config = bot_config.get("max_claims_per_user", {})
        max_claims = max_claims_config.get(user_type, 2 if is_paid else 1)
        
        # Get current claim count
        claim_info = get_user_claim_count(user_id)
        claims_in_period = claim_info.get("claims_in_period", 0)
        
        if claims_in_period >= max_claims:
            return False, f"❌ You have reached your limit of {max_claims} claim(s) per cooldown period. Please wait before claiming again."
        
        remaining_claims = max_claims - claims_in_period
        return True, f"✅ You can make {remaining_claims} more claim(s) in this period."
        
    except Exception as e:
        logger.error(f"Error checking if user can claim: {e}")
        return False, f"❌ Error checking claim limit: {str(e)}"

def reset_user_claims_for_period(user_id: str) -> tuple[bool, str]:
    """
    Manually reset a user's claim count for the current period.
    (Useful for admin operations)
    
    Args:
        user_id (str): The user ID to reset
        
    Returns:
        tuple[bool, str]: (success, message)
    """
    try:
        user_id_str = str(user_id)
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        
        if user_id_str not in claims_data:
            return False, f"❌ User {user_id_str} has no claims to reset."
        
        # Get current time and claim limit hours
        now = datetime.datetime.now()
        claim_limit_hours = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
        period_start = now - datetime.timedelta(hours=claim_limit_hours)
        
        # Remove claims from current period
        original_count = len(claims_data[user_id_str])
        claims_data[user_id_str] = [
            claim for claim in claims_data[user_id_str]
            if not (datetime.datetime.fromisoformat(claim.get('claim_date', claim.get('timestamp', ''))) >= period_start)
        ]
        
        removed_count = original_count - len(claims_data[user_id_str])
        save_json(CLAIMED_ACCOUNTS_FILE, claims_data)
        
        logger.info(f"Reset {removed_count} claims for user {user_id_str}")
        return True, f"✅ Reset {removed_count} claim(s) for user {user_id_str} in the current period."
        
    except Exception as e:
        logger.error(f"Error resetting user claims: {e}")
        return False, f"❌ Error resetting claims: {str(e)}"

def get_user_claim_status(user_id: str, is_paid: bool = False) -> dict:
    """
    Get comprehensive claim status for a user including count and limits.
    
    Args:
        user_id (str): The user ID to check
        is_paid (bool): Whether the user is paid (True) or free (False)
        
    Returns:
        dict: Comprehensive claim status information
    """
    try:
        user_type = "paid" if is_paid else "free"
        user_id_str = str(user_id)
        
        # Get max claims for this user type
        max_claims_config = bot_config.get("max_claims_per_user", {})
        max_claims = max_claims_config.get(user_type, 2 if is_paid else 1)
        
        # Get claim count
        claim_info = get_user_claim_count(user_id_str)
        claims_in_period = claim_info.get("claims_in_period", 0)
        total_claims = claim_info.get("total_claims", 0)
        period_hours = claim_info.get("period_hours", 24)
        
        # Calculate remaining claims
        remaining_claims = max(0, max_claims - claims_in_period)
        
        # Calculate claim percentage
        claim_percentage = (claims_in_period / max_claims * 100) if max_claims > 0 else 0
        
        return {
            "user_id": user_id_str,
            "user_type": user_type,
            "max_claims": max_claims,
            "claims_in_period": claims_in_period,
            "remaining_claims": remaining_claims,
            "total_claims_all_time": total_claims,
            "period_hours": period_hours,
            "claim_percentage": round(claim_percentage, 2),
            "can_claim": remaining_claims > 0,
            "message": f"User '{user_id_str}' ({user_type}): {claims_in_period}/{max_claims} claims used ({claim_percentage:.1f}%)"
        }
        
    except Exception as e:
        logger.error(f"Error getting user claim status: {e}")
        return {"status": "error", "message": f"Error retrieving claim status: {str(e)}"}

def get_all_users_claim_stats() -> dict:
    """
    Get claim statistics for all users.
    
    Returns:
        dict: Statistics including top claimers and usage patterns
    """
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        users_data = load_json(USERS_JSON_FILE)
        
        # Get current time and claim limit hours
        now = datetime.datetime.now()
        claim_limit_hours = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
        period_start = now - datetime.timedelta(hours=claim_limit_hours)
        
        user_stats = []
        
        for user_id_str, claims_list in claims_data.items():
            # Count claims in current period
            claims_in_period = 0
            for claim in claims_list:
                claim_date_str = claim.get('claim_date') or claim.get('timestamp')
                if claim_date_str:
                    try:
                        claim_time = datetime.datetime.fromisoformat(claim_date_str)
                    except Exception:
                        try:
                            claim_time = datetime.datetime.strptime(claim_date_str, '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            continue
                    
                    if claim_time >= period_start:
                        claims_in_period += 1
            
            # Get user info
            user_info = users_data.get(user_id_str, {})
            is_paid = user_info.get('premium_until', '') != ''
            
            user_stats.append({
                "user_id": user_id_str,
                "username": user_info.get('username', 'Unknown'),
                "total_claims": len(claims_list),
                "claims_in_period": claims_in_period,
                "is_paid": is_paid,
                "user_type": "paid" if is_paid else "free"
            })
        
        # Sort by claims in period
        user_stats.sort(key=lambda x: x['claims_in_period'], reverse=True)
        
        return {
            "total_users": len(user_stats),
            "period_hours": claim_limit_hours,
            "top_claimers": user_stats[:10],
            "all_users": user_stats
        }
        
    except Exception as e:
        logger.error(f"Error getting claim statistics: {e}")
        return {"status": "error", "message": f"Error retrieving claim statistics: {str(e)}"}



# ==================== UNBAN ALL USERS FUNCTION ====================

def unban_all_permanently_banned_users() -> tuple[int, int]:
    """
    Unban all permanently banned users.
    
    Returns:
        tuple[int, int]: (unbanned_count, total_count)
            - unbanned_count: Number of users successfully unbanned
            - total_count: Total number of users that were banned
    """
    try:
        global permanent_bans
        
        total_count = len(permanent_bans)
        unbanned_count = 0
        
        if total_count == 0:
            return 0, 0
        
        # Create a copy to iterate over
        bans_to_remove = list(permanent_bans)
        
        for user_id in bans_to_remove:
            try:
                permanent_bans.remove(user_id)
                unbanned_count += 1
                logger.info(f"Unbanned user {user_id}")
            except Exception as e:
                logger.error(f"Error unbanning user {user_id}: {e}")
        
        # Save the updated bans
        save_banned_users()
        
        logger.info(f"Unban all operation completed: {unbanned_count}/{total_count} users unbanned")
        return unbanned_count, total_count
        
    except Exception as e:
        logger.error(f"Error in unban_all_permanently_banned_users: {e}")
        return 0, 0



#   return False

# ------------------ BOT STATUS MANAGEMENT ------------------
def get_bot_status():
    bot_status_data = load_json(BOT_STATUS_FILE)
    return bot_status_data.get("enabled", True)

def set_bot_status(enabled: bool):
    bot_status_data = {"enabled": enabled}
    save_json(BOT_STATUS_FILE, bot_status_data)
    global bot_enabled
    bot_enabled = enabled
    logger.info(f"Bot status set to: {'enabled' if enabled else 'disabled'}")
    return True

# ------------------ CLAIMED ACCOUNTS TRACKING ------------------
def log_claimed_account(user_id: str, username: str, service_key: str, account_content: str):
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        claim_id = str(uuid.uuid4())

        claim_record = {
            'claim_id': claim_id,
            'user_id': user_id,
            'username': username,
            'service_key': service_key,
            'account_content': account_content[:500],
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'claim_date': datetime.datetime.now().isoformat()
        }

        if user_id not in claims_data:
            claims_data[user_id] = []

        claims_data[user_id].append(claim_record)

        if len(claims_data[user_id]) > 50:
            claims_data[user_id] = claims_data[user_id][-50:]

        save_json(CLAIMED_ACCOUNTS_FILE, claims_data)
        logger.info(f"Claim logged for user {user_id} ({username}) on service {service_key}")
        return True
    except Exception as e:
        logger.error(f"Error logging claim: {e}")
        return False

def get_user_claims(user_id: str, limit: int = 20):
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        user_claims = claims_data.get(str(user_id), [])
        user_claims.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return user_claims[:limit]
    except Exception as e:
        logger.error(f"Error getting user claims: {e}")
        return []

def get_all_claims(limit: int = 50):
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        all_claims = []

        for user_id, claims_list in claims_data.items():
            for claim in claims_list:
                claim['user_id'] = user_id
                all_claims.append(claim)

        all_claims.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return all_claims[:limit]
    except Exception as e:
        logger.error(f"Error getting all claims: {e}")
        return []

def get_claims_by_service(service_key: str, limit: int = 50):
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        service_claims = []

        for user_id, claims_list in claims_data.items():
            for claim in claims_list:
                if claim.get('service_key') == service_key:
                    claim['user_id'] = user_id
                    service_claims.append(claim)

        service_claims.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return service_claims[:limit]
    except Exception as e:
        logger.error(f"Error getting claims by service: {e}")
        return []

# ------------------ CLAIM LIMIT ENFORCEMENT ------------------
def can_user_claim_service(user_id: str, service_key: str) -> tuple[bool, int]:
    """
    Check whether a user can claim another account from the specified service.
    Returns a tuple (can_claim, wait_seconds). If can_claim is False,
    wait_seconds indicates how long the user must wait (in seconds) before
    claiming again. A return value of (True, 0) means the user may claim
    immediately. The cooldown is controlled via the global config
    'claim_limit_hours' and optionally per‑service overrides in
    'service_claim_limit_hours'.
    """
    try:
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        user_claims = claims_data.get(str(user_id), [])
        last_time = None
        # Find the most recent claim for this service
        for claim in user_claims:
            if claim.get('service_key') == service_key:
                claim_date_str = claim.get('claim_date') or claim.get('timestamp')
                claim_time = None
                # Try parsing ISO format first, fallback to legacy format
                if claim_date_str:
                    try:
                        claim_time = datetime.datetime.fromisoformat(claim_date_str)
                    except Exception:
                        try:
                            claim_time = datetime.datetime.strptime(claim_date_str, '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            claim_time = None
                if claim_time and (last_time is None or claim_time > last_time):
                    last_time = claim_time
        # If no prior claim, user can claim immediately
        if not last_time:
            return True, 0
        # Otherwise, compute remaining cooldown
        now = datetime.datetime.now()
        global_limit = bot_config.get('claim_limit_hours', DEFAULT_BOT_CONFIG.get('claim_limit_hours', 24))
        service_limits = bot_config.get('service_claim_limit_hours', {})
        service_limit = service_limits.get(service_key, global_limit)
        elapsed_seconds = (now - last_time).total_seconds()
        required_seconds = int(service_limit * 3600)
        wait_seconds = required_seconds - int(elapsed_seconds)
        if wait_seconds <= 0:
            return True, 0
        return False, wait_seconds
    except Exception as e:
        logger.error(f"Error determining claim limit for user {user_id} and service {service_key}: {e}")
        return True, 0

# ------------------ CLAIM NOTIFICATION SYSTEM ------------------
async def send_claim_notification(user_id: str, username: str, service_key: str, account_name: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Send notification when a user claims an account.
    Notifications can be sent to admins, a log channel, both, or none based on config.
    """
    try:
        notification_mode = bot_config.get('claim_notification_mode', 'admins')
        
        if notification_mode == 'none':
            return
        
        # Get service display name
        service_name = services.get(service_key, {}).get('text', service_key)
        
        # Format the notification message
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_display = format_username_display(username) if username else f"User {user_id}"
        user_link = format_user_link(user_id, username)
        
        notification_message = (
            f"\U0001F4E6 <b>Account Claimed</b>\n\n"
            f"\U0001F464 <b>User:</b> {user_link}\n"
            f"\U0001F194 <b>User ID:</b> <code>{user_id}</code>\n"
            f"\U0001F4F1 <b>Service:</b> {html.escape(service_name)}\n"
            f"\U0001F4DD <b>Account:</b> <code>{html.escape(account_name[:100])}{'...' if len(account_name) > 100 else ''}</code>\n"
            f"\U0001F552 <b>Time:</b> {timestamp}"
        )
        
        # Send to admins if mode is 'admins' or 'both'
        if notification_mode in ['admins', 'both']:
            for admin_id in admin_user_ids:
                try:
                    await safe_send_message(
                        admin_id,
                        notification_message,
                        context,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Failed to send claim notification to admin {admin_id}: {e}")
        
        # Send to log channel if mode is 'log_channel' or 'both'
        if notification_mode in ['log_channel', 'both']:
            log_channel = bot_config.get('log_channel')
            if log_channel and log_channel.get('chat_id'):
                try:
                    await safe_send_message(
                        log_channel['chat_id'],
                        notification_message,
                        context,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Failed to send claim notification to log channel: {e}")
        
        logger.info(f"Claim notification sent for user {user_id} claiming {service_key}")
        
    except Exception as e:
        logger.error(f"Error sending claim notification: {e}")

# ------------------ USERNAME DISPLAY HELPER FUNCTIONS ------------------
def is_fallback_username(username: str) -> bool:
    """
    Determine if a username is a fallback generated by the bot (e.g., 'user123456').
    A fallback username typically starts with 'user' followed by digits.
    """
    return bool(username) and username.startswith('user') and username[4:].isdigit()

def format_username_display(username: str) -> str:
    """
    Format a username for display. Real Telegram usernames are prefixed with '@'.
    Fallback usernames (generated by the bot when a real username is absent) are displayed
    without the '@' prefix.
    """
    if not username:
        return ''
    return username if is_fallback_username(username) else f"@{username}"

def format_user_link(user_id: str, username: str) -> str:
    """
    Create a clickable link to a Telegram user given their ID and username. If a real
    username exists, it's prefixed with '@'. Fallback usernames are displayed without
    the '@' prefix. If no username is provided, the link shows the user ID.
    """
    # Determine the display text based on the username
    display_text = format_username_display(username) if username else f"User {user_id}"
    return f'<a href="tg://user?id={user_id}">{display_text}</a>'

# ------------------ INITIALIZATION ------------------

def initialize_bot_systems():
    global services
    initialize_data_files()
    load_config()
    load_admins()
    load_banned_users()


    logger.info("Initializing directories...")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    (TEMP_DIR / "cookies").mkdir(parents=True, exist_ok=True)
    (TEMP_DIR / "content_files").mkdir(parents=True, exist_ok=True)
    (TEMP_DIR / "uploads").mkdir(parents=True, exist_ok=True)

    default_bg_path_str = get_safe_background()
    if not default_bg_path_str:
        default_bg_path_str = str(BASE_DIR / DEFAULT_BACKGROUND_IMAGE_FILENAME)
        logger.warning(f"No safe background found, using placeholder path: {default_bg_path_str}")

    services = {
        'crunchyroll': {'backgrounds': [default_bg_path_str], 'text': 'Crunchyroll Premium Accounts', 'category': 'Streaming', 'from_code': True},
        'steam': {'backgrounds': [default_bg_path_str], 'text': 'Steam Game Accounts', 'category': 'Gaming', 'from_code': True},
        'netflixAccounts': {'backgrounds': [default_bg_path_str], 'text': 'Netflix Accounts', 'category': 'Streaming', 'from_code': True},
        'dazn': {'backgrounds': [default_bg_path_str], 'text': 'DAZN Premium Accounts', 'category': 'Streaming', 'from_code': True},
        'disney': {'backgrounds': [default_bg_path_str], 'text': 'Disney+ Accounts', 'category': 'Streaming', 'from_code': True},
        'duolingo': {'backgrounds': [default_bg_path_str], 'text': 'Duolingo Accounts', 'category': 'Education', 'from_code': True},
        'shahid': {'backgrounds': [default_bg_path_str], 'text': 'Shahid Accounts', 'category': 'Streaming', 'from_code': True},
        'ubisoftAccounts': {'backgrounds': [default_bg_path_str], 'text': 'Ubisoft Accounts', 'category': 'Gaming', 'from_code': True},
        'hotmailAccounts': {'backgrounds': [default_bg_path_str], 'text': 'Hotmail Hits', 'category': 'Utilities', 'from_code': True},
        'xboxAccounts': {'backgrounds': [default_bg_path_str], 'text': 'Xbox Accounts', 'category': 'Gaming', 'from_code': True},
        'rdpAccounts': {'backgrounds': [default_bg_path_str], 'text': 'RDP Accounts', 'category': 'Utilities', 'from_code': True},
        'netflixCookies': {'backgrounds': [default_bg_path_str], 'text': 'Netflix Cookies', 'category': 'Cookies', 'from_code': True},
        'spotifyCookies': {'backgrounds': [default_bg_path_str], 'text': 'Spotify Cookies', 'category': 'Cookies', 'from_code': True},
        'primeCookies': {'backgrounds': [default_bg_path_str], 'text': 'Prime Video Cookies', 'category': 'Cookies', 'from_code': True},
        'chatgptCookies': {'backgrounds': [default_bg_path_str], 'text': 'ChatGPT Cookies', 'category': 'Cookies', 'from_code': True},
        'udemy': {'backgrounds': [default_bg_path_str], 'text': 'Udemy Premium Accounts', 'category': 'Education', 'from_code': True},
    }

    load_services()
    initialize_points_and_prices()
    logger.info("Initialization complete.")

# ------------------ USER & POINTS SYSTEM ------------------
def ensure_user_exists(user_id, username=None, referral_code_override=None):
    user_id_str = str(user_id)
    users_data = load_json(USERS_JSON_FILE)

    if user_id_str in users_data:
        user_data = users_data[user_id_str]
        if username and username != user_data.get('username'):
            user_data['username'] = username
            users_data[user_id_str] = user_data
            save_json(USERS_JSON_FILE, users_data)

        if referral_code_override and not user_data.get('referral_code'):
            user_data['referral_code'] = referral_code_override
            users_data[user_id_str] = user_data
            save_json(USERS_JSON_FILE, users_data)

        return user_data.get('referral_code') or referral_code_override or None

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_referral_code = referral_code_override if referral_code_override else str(uuid.uuid4())[:6].upper()

    users_data[user_id_str] = {
        'user_id': user_id_str,
        'username': username or '',
        'full_name': '', # Will be updated on next interaction
        'join_date': now,
        'premium_until': '',
        'referral_code': new_referral_code,
        'referred_by': '',
        'total_referrals': 0,
        'bonus_given': False,
        'total_claims': 0
    }

    save_json(USERS_JSON_FILE, users_data)
    logger.info(f"New user {user_id_str} ({username or 'N/A'}) added.")

    update_user_points(user_id_str, username, 0)
    return new_referral_code

def check_user_premium_status(user_id):
    user_id_str = str(user_id)
    if is_admin(int(user_id_str)):
        return True, "Lifetime (Admin)"

    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(user_id_str, {})

    premium_until = user_data.get('premium_until')
    if premium_until:
        try:
            p_until_dt = datetime.datetime.strptime(premium_until, '%Y-%m-%d %H:%M:%S')
            if p_until_dt > datetime.datetime.now():
                return True, p_until_dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            logger.error(f"Invalid premium_until date format for user {user_id_str}: {premium_until}")

    return False, None

def is_user_premium(user_id):
    """Helper function to check if a user has premium status."""
    is_premium, _ = check_user_premium_status(user_id)
    return is_premium

def update_user_premium_status(user_id, username, key_type, days_override=None):
    user_id_str = str(user_id)
    
    # Handle premium removal
    if days_override == 0 or key_type == "remove":
        users_data = load_json(USERS_JSON_FILE)
        if user_id_str in users_data:
            users_data[user_id_str]['premium_until'] = ''
            save_json(USERS_JSON_FILE, users_data)
            logger.info(f"Premium removed for user {user_id_str}")
            return True
        return False

    key_validity_config = bot_config.get("key_validity_days", DEFAULT_BOT_CONFIG["key_validity_days"])

    if days_override is not None:
        duration_days = days_override
    elif key_type == "lifetime":
        duration_days = key_validity_config.get("lifetime", 3650)
    else:
        duration_days = key_validity_config.get(key_type, 30)

    new_premium_until_dt = datetime.datetime.now() + datetime.timedelta(days=duration_days)
    new_premium_until_str = new_premium_until_dt.strftime('%Y-%m-%d %H:%M:%S')

    ensure_user_exists(user_id_str, username)
    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(user_id_str, {})

    if user_data:
        current_premium_until_str = user_data.get('premium_until')
        if current_premium_until_str:
            try:
                current_p_dt = datetime.datetime.strptime(current_premium_until_str, '%Y-%m-%d %H:%M:%S')
                if current_p_dt > datetime.datetime.now():
                    if key_type == "lifetime" or duration_days == key_validity_config.get("lifetime"):
                        final_premium = new_premium_until_str
                    else:
                        final_premium = (current_p_dt + datetime.timedelta(days=duration_days)).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    final_premium = new_premium_until_str
            except ValueError:
                final_premium = new_premium_until_str
        else:
            final_premium = new_premium_until_str

        user_data['premium_until'] = final_premium
        if username and (not user_data.get('username') or user_data.get('username') != username):
            user_data['username'] = username

        users_data[user_id_str] = user_data
        save_json(USERS_JSON_FILE, users_data)
        logger.info(f"User {user_id_str} premium status updated. Type: {key_type}, Duration: {duration_days} days. New Expiry: {final_premium}")
        return True
    else:
        logger.error(f"User {user_id_str} not found during premium update.")
        return False

def get_user_points(user_id):
    user_id_str = str(user_id)
    default_join_points = bot_config.get("default_points_on_join", DEFAULT_BOT_CONFIG["default_points_on_join"])

    points_data = read_csv(USER_POINTS_FILE)
    for row in points_data:
        if row['user_id'] == user_id_str:
            try:
                points = int(row['points'])
                if points == 30 and default_join_points == 1:
                    update_user_points(user_id, None, 1 - points)
                    return 1
                return points
            except (ValueError, KeyError):
                update_user_points(user_id, None, default_join_points)
                return default_join_points

    update_user_points(user_id, None, default_join_points)
    return default_join_points

def update_user_points(user_id, username, points_to_add_or_subtract):
    user_id_str = str(user_id)
    points_data = read_csv(USER_POINTS_FILE)

    default_join_pts = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    user_found = False
    for i, row in enumerate(points_data):
        if row['user_id'] == user_id_str:
            try:
                current_points = int(row['points'])
            except (ValueError, KeyError):
                current_points = default_join_pts

            new_points = current_points + points_to_add_or_subtract
            points_data[i] = {
                'user_id': user_id_str,
                'username': username or row.get('username', f"user{user_id_str}"),
                'points': str(new_points),
                'last_updated': now_str
            }
            user_found = True
            break

    if not user_found:
        new_points = default_join_pts + points_to_add_or_subtract
        points_data.append({
            'user_id': user_id_str,
            'username': username or f"user{user_id_str}",
            'points': str(new_points),
            'last_updated': now_str
        })

    write_csv(USER_POINTS_FILE, points_data, ['user_id', 'username', 'points', 'last_updated'])
    return True

def deduct_points(user_id, points_to_deduct):
    return update_user_points(user_id, None, -points_to_deduct)

def set_points_for_all_users(points_value: int):
    try:
        points_data = read_csv(USER_POINTS_FILE)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        updated_count = 0
        for i, row in enumerate(points_data):
            points_data[i] = {
                'user_id': row['user_id'],
                'username': row.get('username', f"user{row['user_id']}"),
                'points': str(points_value),
                'last_updated': now_str
            }
            updated_count += 1

        write_csv(USER_POINTS_FILE, points_data, ['user_id', 'username', 'points', 'last_updated'])
        logger.info(f"Set points to {points_value} for {updated_count} users")
        return updated_count
    except Exception as e:
        logger.error(f"Error setting points for all users: {e}")
        return 0

def recalculate_points_based_on_referrals():
    """Recalculate all users' points based on their referrals and purchases"""
    try:
        users_data = load_json(USERS_JSON_FILE)
        points_per_referral = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
        default_points_on_join = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])

        # Load service prices
        service_prices_data = load_json(SERVICE_PRICES_FILE)

        # Load all claims data
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)

        updated_count = 0
        for user_id, user_data in users_data.items():
            # 1. Calculate points from referrals
            referral_count = int(user_data.get('total_referrals', 0))
            points_from_referrals = referral_count * points_per_referral

            # 2. Add default points on join
            base_points = points_from_referrals + default_points_on_join

            # 3. Calculate points spent on services
            user_claims = claims_data.get(str(user_id), [])
            total_spent = 0

            # Group claims by service to count purchases
            service_counts = {}
            for claim in user_claims:
                service_key = claim.get('service_key')
                if service_key:
                    service_counts[service_key] = service_counts.get(service_key, 0) + 1

            # Calculate total spent based on service prices
            for service_key, count in service_counts.items():
                service_price = 100  # Default price
                if service_key in service_prices_data:
                    price_data = service_prices_data[service_key]
                    if isinstance(price_data, dict):
                        service_price = price_data.get('points_cost', 100)
                    else:
                        service_price = price_data  # Old format
                total_spent += service_price * count

            # 4. Calculate expected points
            expected_points = base_points - total_spent

            # Ensure points don't go negative (minimum 0)
            if expected_points < 0:
                expected_points = 0

            # 5. Get current points and compare
            current_points = get_user_points(user_id)

            if expected_points != current_points:
                # Calculate the difference
                point_difference = expected_points - current_points

                # Update user points with the difference
                update_user_points(user_id, user_data.get('username'), point_difference)

                updated_count += 1
                logger.info(f"Recalculated points for user {user_id}: {current_points} -> {expected_points} "
                           f"(Referrals: {referral_count}×{points_per_referral}=+{points_from_referrals}, "
                           f"Default: +{default_points_on_join}, Spent: -{total_spent})")

        logger.info(f"Recalculated points for {updated_count} users based on referrals and purchases")
        return updated_count
    except Exception as e:
        logger.error(f"Error recalculating points based on referrals and purchases: {e}", exc_info=True)
        return 0

# ------------------ NEW HELPER FUNCTIONS FOR TRANSFERS AND REDEMPTIONS ------------------

def get_user_transfers(user_id: str) -> list:
    """
    Get all transfers involving a specific user (both sent and received).
    Returns a list of transfer records.
    """
    try:
        user_id_str = str(user_id)
        transfers_data = load_json(TRANSFERS_FILE)

        user_transfers = []
        for transfer_id, transfer in transfers_data.items():
            if transfer.get('sender_id') == user_id_str or transfer.get('recipient_id') == user_id_str:
                transfer['transfer_id'] = transfer_id
                user_transfers.append(transfer)

        # Sort by timestamp (most recent first)
        user_transfers.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return user_transfers
    except Exception as e:
        logger.error(f"Error getting user transfers for {user_id}: {e}")
        return []

def get_all_transfers(limit: int = 100) -> list:
    """
    Get all transfers in the system.
    Returns a list of transfer records sorted by timestamp.
    """
    try:
        transfers_data = load_json(TRANSFERS_FILE)

        all_transfers = []
        for transfer_id, transfer in transfers_data.items():
            transfer['transfer_id'] = transfer_id
            all_transfers.append(transfer)

        # Sort by timestamp (most recent first)
        all_transfers.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return all_transfers[:limit]
    except Exception as e:
        logger.error(f"Error getting all transfers: {e}")
        return []

def get_user_key_redemptions(user_id: str) -> list:
    """
    Get all key redemptions for a specific user.
    Returns a list of redemption records with type ('premium' or 'points').
    """
    try:
        user_id_str = str(user_id)
        redemptions = []

        # Check premium keys
        keys_data = load_json(KEYS_FILE)
        for key, key_info in keys_data.items():
            if key_info.get('used_by') == user_id_str and key_info.get('status') == 'used':
                redemptions.append({
                    'key': key,
                    'type': 'premium',
                    'key_type': key_info.get('type', 'lifetime'),
                    'used_at': key_info.get('used_at', ''),
                    'created_at': key_info.get('created_at', '')
                })

        # Check point keys
        point_keys_data = load_json(POINT_KEYS_FILE)
        for key, key_info in point_keys_data.items():
            if key_info.get('used_by') == user_id_str and key_info.get('status') == 'used':
                redemptions.append({
                    'key': key,
                    'type': 'points',
                    'points': key_info.get('points', 0),
                    'used_at': key_info.get('used_at', ''),
                    'created_at': key_info.get('created_at', '')
                })

        # Sort by used_at timestamp (most recent first)
        redemptions.sort(key=lambda x: x.get('used_at', ''), reverse=True)
        return redemptions
    except Exception as e:
        logger.error(f"Error getting user key redemptions for {user_id}: {e}")
        return []

def get_transfer_history_text(user_id: str, limit: int = 5) -> str:
    """
    Generate formatted text showing transfer history for a user.
    """
    transfers = get_user_transfers(user_id)
    if not transfers:
        return "  No transfers yet"

    history_lines = []
    user_id_str = str(user_id)

    for i, transfer in enumerate(transfers[:limit]):
        if transfer.get('sender_id') == user_id_str:
            direction = "🔽 Sent"
            other_user = transfer.get('recipient_username', 'Unknown')
        else:
            direction = "🔼 Received"
            other_user = transfer.get('sender_username', 'Unknown')

        amount = transfer.get('amount', 0)
        timestamp = transfer.get('timestamp', '')[:10]  # Just the date
        history_lines.append(f"  • {direction} {amount} pts {other_user} on {timestamp}")

    if len(transfers) > limit:
        history_lines.append(f"  • ... and {len(transfers) - limit} more transfers")

    return "\n".join(history_lines)

def get_redemption_history_text(user_id: str, limit: int = 5) -> str:
    """
    Generate formatted text showing redemption history for a user.
    """
    redemptions = get_user_key_redemptions(user_id)
    if not redemptions:
        return "  No redemptions yet"

    history_lines = []

    for i, redemption in enumerate(redemptions[:limit]):
        if redemption.get('type') == 'premium':
            key_type = redemption.get('key_type', 'lifetime')
            key_short = redemption.get('key', '')[:8] + '...'
            history_lines.append(f"  • 💎 Premium ({key_type}) - Key: {key_short} on {redemption.get('used_at', '')[:10]}")
        else:
            points = redemption.get('points', 0)
            key_short = redemption.get('key', '')[:8] + '...'
            history_lines.append(f"  • 💰 {points} points - Key: {key_short} on {redemption.get('used_at', '')[:10]}")

    if len(redemptions) > limit:
        history_lines.append(f"  • ... and {len(redemptions) - limit} more redemptions")

    return "\n".join(history_lines)

# ------------------ KEYS & REFERRALS ------------------
def generate_key(admin_user_id, key_type="lifetime", validity_days=None):
    key_type = "lifetime"
    key_validity_config = bot_config.get("key_validity_days", DEFAULT_BOT_CONFIG["key_validity_days"])
    validity_days = key_validity_config.get("lifetime", 3650)

    key = str(uuid.uuid4()).upper()
    created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    expires_at = (datetime.datetime.now() + datetime.timedelta(days=validity_days)).strftime('%Y-%m-%d %H:%M:%S')

    keys_data = load_json(KEYS_FILE)
    keys_data[key] = {
        'created_by': str(admin_user_id),
        'created_at': created_at,
        'expires_at': expires_at,
        'used_by': '',
        'used_at': '',
        'type': key_type,
        'status': 'active'
    }
    save_json(KEYS_FILE, keys_data)
    logger.info(f"Generated lifetime key: {key}, Validity: {validity_days} days")
    return key

def generate_point_key(admin_user_id, points):
    key = str(uuid.uuid4()).upper()
    created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    point_keys_data = load_json(POINT_KEYS_FILE)
    point_keys_data[key] = {
        'created_by': str(admin_user_id),
        'created_at': created_at,
        'points': points,
        'used_by': '',
        'used_at': '',
        'status': 'active'
    }
    save_json(POINT_KEYS_FILE, point_keys_data)
    logger.info(f"Generated point key: {key}, Points: {points}")
    return key

def validate_key(key_input, user_id, username):
    keys_data = load_json(KEYS_FILE)

    if key_input not in keys_data:
        logger.warning(f"Key {key_input} not found during validation.")
        return False, None

    key_data = keys_data[key_input]
    if key_data.get('status') != 'active':
        return False, None

    expires_at = key_data.get('expires_at')
    try:
        expires_at_dt = datetime.datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S') if expires_at else None
        if expires_at_dt and expires_at_dt > datetime.datetime.now():
            key_type_found = key_data.get('type')
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            key_data['used_by'] = str(user_id)
            key_data['used_at'] = now_str
            key_data['status'] = 'used'
            keys_data[key_input] = key_data
            save_json(KEYS_FILE, keys_data)

            return True, key_type_found
        else:
            key_data['status'] = 'expired'
            keys_data[key_input] = key_data
            save_json(KEYS_FILE, keys_data)
            logger.info(f"Key {key_input} is expired (Expiry: {expires_at}).")
    except ValueError as ve:
        logger.error(f"Invalid date format for key {key_input}: {expires_at}. Error: {ve}")
        key_data['status'] = 'invalid_date'
        keys_data[key_input] = key_data
        save_json(KEYS_FILE, keys_data)

    return False, None

def validate_point_key(key_input, user_id, username):
    point_keys_data = load_json(POINT_KEYS_FILE)

    if key_input not in point_keys_data:
        logger.warning(f"Point key {key_input} not found during validation.")
        return False, None

    key_data = point_keys_data[key_input]
    if key_data.get('status') != 'active':
        return False, None

    points_found = key_data.get('points')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    key_data['used_by'] = str(user_id)
    key_data['used_at'] = now_str
    key_data['status'] = 'used'
    point_keys_data[key_input] = key_data
    save_json(POINT_KEYS_FILE, point_keys_data)

    return True, points_found

def get_user_referral_code(user_id):
    user_id_str = str(user_id)
    users_data = load_json(USERS_JSON_FILE)

    if user_id_str in users_data:
        ref_code = users_data[user_id_str].get('referral_code')
        if ref_code:
            return ref_code

    new_code = str(uuid.uuid4())[:6].upper()
    if user_id_str in users_data:
        users_data[user_id_str]['referral_code'] = new_code
    else:
        users_data[user_id_str] = {'referral_code': new_code}

    save_json(USERS_JSON_FILE, users_data)
    return new_code

def find_user_by_referral_code(ref_code):
    users_data = load_json(USERS_JSON_FILE)
    for uid, data in users_data.items():
        if data.get('referral_code') == ref_code:
            return uid
    return None

def verify_referral_code(user_id, ref_code_to_check):
    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(str(user_id), {})
    return user_data.get('referral_code') == ref_code_to_check

def process_referral(referrer_id, referee_id):
    ref_id_str, ree_id_str = str(referrer_id), str(referee_id)

    if ref_id_str == ree_id_str:
        logger.info(f"User {ref_id_str} cannot refer themselves.")
        return False, False, 0

    referrals_data = read_csv(REFERRALS_FILE)
    for ref_row in referrals_data:
        if ref_row.get('referrer_id') == ref_id_str and ref_row.get('referee_id') == ree_id_str:
            logger.info(f"Referral from {ref_id_str} to {ree_id_str} already processed.")
            return False, False, get_referral_count(ref_id_str)

    new_ref_id = str(uuid.uuid4())
    append_csv_row(REFERRALS_FILE, {
        'referral_id': new_ref_id,
        'referrer_id': ref_id_str,
        'referee_id': ree_id_str,
        'referral_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'active'
    }, ['referral_id', 'referrer_id', 'referee_id', 'referral_date', 'status'])

    ensure_user_exists(ref_id_str, None)
    ensure_user_exists(ree_id_str, None)

    users_data = load_json(USERS_JSON_FILE)
    referrer_data = users_data.get(ref_id_str, {})
    current_refs = int(referrer_data.get('total_referrals', 0))
    new_ref_count = current_refs + 1

    referrer_data['total_referrals'] = new_ref_count
    users_data[ref_id_str] = referrer_data

    referee_data = users_data.get(ree_id_str, {})
    if not referee_data.get('referred_by'):
        referee_data['referred_by'] = ref_id_str
        users_data[ree_id_str] = referee_data

    save_json(USERS_JSON_FILE, users_data)

    points_per_ref = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
    update_user_points(ref_id_str, None, points_per_ref)
    update_user_points(ree_id_str, None, points_per_ref // 2)

    logger.info(f"Referral processed: {ref_id_str} -> {ree_id_str}. Referrer new count: {new_ref_count}.")
    return True, False, new_ref_count

def get_referral_count(user_id):
    user_id_str = str(user_id)
    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(user_id_str, {})
    total_referrals = user_data.get('total_referrals')
    return int(total_referrals) if total_referrals is not None else 0

def get_user_referees(user_id: str) -> list:
    """
    Return a list of referee IDs (as strings) for the given referrer.
    Reads from the referrals CSV file. Only includes entries where
    the referrer_id matches user_id regardless of referral status.
    """
    try:
        referrals = read_csv(REFERRALS_FILE)
        uid_str = str(user_id)
        referees = [row['referee_id'] for row in referrals if row.get('referrer_id') == uid_str]
        return referees
    except Exception as e:
        logger.error(f"Error reading referees for user {user_id}: {e}")
        return []

def get_user_referee_usernames(user_id: str) -> list:
    """
    Return a list of usernames for referees of the given referrer. If a referee
    does not have a username, their user ID is returned instead.
    """
    referees = get_user_referees(user_id)
    users_data = load_json(USERS_JSON_FILE)
    names = []
    for rid in referees:
        user_data = users_data.get(str(rid), {})
        uname = user_data.get('username')
        if not uname:
            uname = f"user{rid}"
        names.append(uname)
    return names

def get_referral_statistics():
    try:
        users_data = load_json(USERS_JSON_FILE)
        referrals_data = read_csv(REFERRALS_FILE)

        stats = {
            'total_referrals': len(referrals_data),
            'active_referrals': len([r for r in referrals_data if r.get('status') == 'active']),
            'top_referrers': [],
            'recent_referrals': []
        }

        referrer_counts = {}
        for user_id, user_data in users_data.items():
            ref_count = int(user_data.get('total_referrals', 0))
            if ref_count > 0:
                referrer_counts[user_id] = {
                    'user_id': user_id,
                    'username': user_data.get('username', f"user{user_id}"),
                    'referral_count': ref_count
                }

        top_referrers = sorted(referrer_counts.values(), key=lambda x: x['referral_count'], reverse=True)
        stats['top_referrers'] = top_referrers[:10]

        recent_refs = sorted(referrals_data, key=lambda x: x.get('referral_date', ''), reverse=True)[:10]
        for ref in recent_refs:
            referrer_id = ref.get('referrer_id')
            referee_id = ref.get('referee_id')
            referrer_name = users_data.get(referrer_id, {}).get('username', f"user{referrer_id}")
            referee_name = users_data.get(referee_id, {}).get('username', f"user{referee_id}")

            stats['recent_referrals'].append({
                'referrer': referrer_name,
                'referee': referee_name,
                'date': ref.get('referral_date', 'Unknown')
            })

        return stats
    except Exception as e:
        logger.error(f"Error getting referral statistics: {e}")
        return None

def export_referral_stats_to_csv():
    try:
        stats = get_referral_statistics()
        if not stats:
            return None

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(['Referral Statistics Export'])
        writer.writerow(['Export Date', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow([])

        writer.writerow(['Overview'])
        writer.writerow(['Total Referrals', stats['total_referrals']])
        writer.writerow(['Active Referrals', stats['active_referrals']])
        writer.writerow([])

        writer.writerow(['Top Referrers'])
        writer.writerow(['Rank', 'User ID', 'Username', 'Referral Count'])
        for i, referrer in enumerate(stats['top_referrers'], 1):
            writer.writerow([i, referrer['user_id'], referrer['username'], referrer['referral_count']])

        writer.writerow([])
        writer.writerow(['Recent Referrals'])
        writer.writerow(['Referrer', 'Referee', 'Date'])
        for ref in stats['recent_referrals']:
            writer.writerow([ref['referrer'], ref['referee'], ref['date']])

        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Error exporting referral stats: {e}")
        return None

# ------------------ SERVICE PRICES & ITEMS ------------------
def initialize_points_and_prices():
    logger.info("Points and prices system check/initialization.")
    if services:
        prices_data = load_json(SERVICE_PRICES_FILE)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for sk, si in services.items():
            if sk not in prices_data:
                prices_data[sk] = {
                    'service_key': sk,
                    'service_name': si.get('text', sk),
                    'points_cost': 100,
                    'last_updated': now_str
                }
                logger.info(f"Added default price for new service '{sk}'.")

        save_json(SERVICE_PRICES_FILE, prices_data)

def get_service_price(service_key):
    prices_data = load_json(SERVICE_PRICES_FILE)
    if service_key in prices_data:
        points_cost = prices_data[service_key].get('points_cost')
        if points_cost is not None:
            try:
                return int(points_cost)
            except (ValueError, TypeError):
                pass

    logger.warning(f"Service {service_key} not found in service_prices. Adding default price 100.")
    update_service_price(service_key, 100)
    return 100

def update_service_price(service_key, new_price):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    s_name = services.get(service_key, {}).get('text', service_key)

    prices_data = load_json(SERVICE_PRICES_FILE)
    prices_data[service_key] = {
        'service_key': service_key,
        'service_name': s_name,
        'points_cost': new_price,
        'last_updated': now
    }
    save_json(SERVICE_PRICES_FILE, prices_data)
    logger.info(f"Updated price for service '{service_key}' ({s_name}) to {new_price} points.")
    return True

def add_accounts_to_service(service_key: str, accounts_list):
    if not accounts_list:
        return True

    service_items_data = load_json(SERVICE_ITEMS_FILE)
    if service_key not in service_items_data:
        service_items_data[service_key] = {'items': {}}
    
    if 'items' not in service_items_data[service_key]:
        service_items_data[service_key]['items'] = {}

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Use a set to avoid adding exact duplicates in the same batch
    unique_new_items = []
    existing_contents = {item['content'] for item in service_items_data[service_key]['items'].values()}
    
    added_count = 0
    for acc_item in accounts_list:
        acc_str = acc_item.strip()
        if not acc_str or acc_str in existing_contents:
            continue

        item_id = str(uuid.uuid4())
        service_items_data[service_key]['items'][item_id] = {
            'content': acc_str,
            'added_at': now_str
        }
        existing_contents.add(acc_str)
        added_count += 1

    save_json(SERVICE_ITEMS_FILE, service_items_data)
    logger.info(f"Added {added_count} unique accounts to service {service_key}.")
    return True

def pop_account_from_service(service_key: str, context: ContextTypes.DEFAULT_TYPE = None, claimed_by_user_id: str = None, claimed_by_username: str = None):
    service_items_data = load_json(SERVICE_ITEMS_FILE)

    if service_key not in service_items_data or not service_items_data[service_key].get('items'):
        return None

    items_dict = service_items_data[service_key]['items']
    if not items_dict:
        return None

    first_item_id = next(iter(items_dict))
    content = items_dict[first_item_id]['content']

    # Get claim time before deleting the item
    claim_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    del items_dict[first_item_id]
    service_items_data[service_key]['items'] = items_dict
    save_json(SERVICE_ITEMS_FILE, service_items_data)

    remaining_stock = len(items_dict)
    low_stock_threshold = bot_config.get("low_stock_threshold", 5)

    # Trigger low stock alert with claim info
    if remaining_stock <= low_stock_threshold and context and claimed_by_username:
        asyncio.create_task(notify_admin_low_stock(
            service_key,
            remaining_stock,
            context,
            claimed_by_user_id,
            claimed_by_username,
            claim_time
        ))

    return content


def get_service_stock(service_key: str):
    service_items_data = load_json(SERVICE_ITEMS_FILE)
    if service_key in service_items_data and 'items' in service_items_data[service_key]:
        return len(service_items_data[service_key]['items'])
    return 0

def remove_all_items_from_service(service_key: str):
    service_items_data = load_json(SERVICE_ITEMS_FILE)

    if service_key in service_items_data:
        item_count = len(service_items_data[service_key].get('items', {}))
        service_items_data[service_key]['items'] = {}
        save_json(SERVICE_ITEMS_FILE, service_items_data)
        logger.info(f"Removed all {item_count} items from service {service_key}")
        return item_count
    return 0

async def notify_admin_low_stock(service_key: str, remaining_stock: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        service_name = services.get(service_key, {}).get('text', service_key)
        low_stock_threshold = bot_config.get("low_stock_threshold", 5)

        message = (
            f"⚠️ <b>LOW STOCK ALERT</b>\n\n"
            f"Service: <b>{service_name}</b>\n"
            f"Key: <code>{service_key}</code>\n"
            f"Remaining Stock: <b>{remaining_stock}</b>\n"
            f"Threshold: {low_stock_threshold}\n\n"
            f"Please add more items to this service."
        )

        logger.warning(f"Low stock alert for {service_key}: {remaining_stock} items left")

        for admin_id in admin_user_ids:
            try:
                await safe_send_message(
                    admin_id,
                    message,
                    context,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to send low stock alert to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error preparing low stock notification for {service_key}: {e}")

# ------------------ FEEDBACK SYSTEM ------------------
def save_feedback_to_file(user_id, username, name, feedback_text, service_key=None):
    try:
        feedback_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now().isoformat()

        feedback_data = {
            'user_id': str(user_id),
            'username': username or 'NoUsername',
            'name': name or 'NoName',
            'feedback': feedback_text,
            'service_key': service_key or 'general',
            'timestamp': timestamp,
            'status': 'new',
            'replied': False
        }

        feedbacks_data = load_json(FEEDBACKS_FILE)
        feedbacks_data[feedback_id] = feedback_data
        save_json(FEEDBACKS_FILE, feedbacks_data)

        logger.info(f"Feedback saved: {feedback_id} from user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        return False

def get_feedbacks_from_file(limit=50):
    try:
        feedbacks_data = load_json(FEEDBACKS_FILE)
        feedback_list = []

        for feedback_id, feedback_data in feedbacks_data.items():
            if isinstance(feedback_data, dict):
                feedback_data['id'] = feedback_id
                feedback_list.append(feedback_data)

        feedback_list.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return feedback_list[:limit]
    except Exception as e:
        logger.error(f"Error getting feedbacks: {e}")
        return []

def update_feedback_status(feedback_id, status, reply_message=None, replied_by=None):
    try:
        feedbacks_data = load_json(FEEDBACKS_FILE)

        if feedback_id not in feedbacks_data:
            return False

        updates = {'status': status}
        if reply_message:
            updates['reply_message'] = reply_message
            updates['replied'] = True
            updates['replied_at'] = datetime.datetime.now().isoformat()
            if replied_by:
                updates['replied_by'] = replied_by

        feedbacks_data[feedback_id].update(updates)
        save_json(FEEDBACKS_FILE, feedbacks_data)
        return True
    except Exception as e:
        logger.error(f"Error updating feedback status: {e}")
        return False

def delete_feedback(feedback_id):
    try:
        feedbacks_data = load_json(FEEDBACKS_FILE)
        if feedback_id in feedbacks_data:
            del feedbacks_data[feedback_id]
            save_json(FEEDBACKS_FILE, feedbacks_data)
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting feedback: {e}")
        return False

async def send_feedback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    if context.user_data.get('awaiting_feedback'):
        feedback_text = update.message.text
        service_key = context.user_data.get('feedback_service', 'general')
        service_name = services.get(service_key, {}).get('text', service_key)

        success = save_feedback_to_file(
            user.id,
            user.username,
            user.full_name,
            feedback_text,
            service_key
        )

        if success:
            # Build a clickable user link. Use the Telegram ID and username (if available).
            user_link = format_user_link(str(user.id), user.username or '')
            feedback_message = (
                f"📩 <b>New Feedback Received!</b>\n\n"
                f"👤 <b>User:</b> {html.escape(user.full_name)} {user_link}\n"
                f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
                f"📱 <b>Service:</b> {service_name} (<code>{service_key}</code>)\n"
                f"⏰ <b>Timestamp:</b> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💬 <b>Feedback Message:</b>\n<code>{html.escape(feedback_text)}</code>"
            )

            for admin_id in admin_user_ids:
                try:
                    await safe_send_message(
                        admin_id,
                        feedback_message,
                        context,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error sending feedback notification to admin {admin_id}: {e}")

            context.user_data.pop('awaiting_feedback', None)
            context.user_data.pop('feedback_service', None)

            confirmation_message = (
                f"✅ <b>Thank you for your feedback!</b>\n\n"
                f"Your feedback about <b>{service_name}</b> has been sent to the admins.\n\n"
                f"We appreciate your input and will review it carefully."
            )

            await safe_send_message(
                user.id,
                confirmation_message,
                context,
                parse_mode=ParseMode.HTML
            )
        else:
            await safe_send_message(
                user.id,
                "❌ <b>Sorry, there was an error saving your feedback.</b>\n\nPlease try again later.",
                context,
                parse_mode=ParseMode.HTML
            )

async def start_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    service_key = 'general'

    if query:
        await query.answer()
        callback_data = query.data

        if (callback_data.startswith('feedback_') and
            not callback_data.startswith('feedback_nav_') and
            not callback_data.startswith('fb_nav_') and
            not callback_data.startswith('fb_reply_') and
            not callback_data.startswith('fb_mark_read_') and
            not callback_data.startswith('fb_mark_new_') and
            not callback_data.startswith('fb_delete_') and
            not callback_data.startswith('fb_confirm_delete_') and
            callback_data != 'fb_cancel_delete' and
            callback_data != 'send_feedback'):

            service_key = callback_data.replace('feedback_', '')
            if service_key in services or service_key == 'general':
                context.user_data['feedback_service'] = service_key
            else:
                context.user_data['feedback_service'] = 'general'
        else:
            if callback_data == 'send_feedback':
                context.user_data['feedback_service'] = 'general'
            else:
                return

        chat_id = query.message.chat_id
        service_name = services.get(service_key, {}).get('text', service_key)
        message = (
            f"💬 <b>Feedback for {html.escape(service_name)}</b>\n\n"
            f"Please write your feedback message below. You can include:\n"
            f"• Suggestions for improvement\n"
            f"• Issues you encountered\n"
            f"• General comments\n\n"
            f"<i>Type /cancel to cancel.</i>"
        )
    else:
        context.user_data['feedback_service'] = service_key
        chat_id = update.effective_chat.id
        message = (
            "💬 <b>Send Feedback</b>\n\n"
            "Please write your feedback message below. You can include:\n"
            "• Suggestions for improvement\n"
            "• Issues you encountered\n"
            "• General comments about the bot\n\n"
            "<i>Type /cancel to cancel.</i>"
        )

    context.user_data['awaiting_feedback'] = True
    await safe_send_message(
        chat_id,
        message,
        context,
        parse_mode=ParseMode.HTML
    )

async def cancel_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting_feedback', None)
    context.user_data.pop('feedback_service', None)
    await safe_send_message(
        update.effective_chat.id,
        "❌ <b>Feedback cancelled.</b>",
        context,
        parse_mode=ParseMode.HTML
    )

async def admin_view_feedbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view feedbacks.",
            parse_mode=ParseMode.HTML
        )
        return

    feedbacks = get_feedbacks_from_file()

    if not feedbacks:
        await query.edit_message_text(
            "📭 <b>No Feedbacks</b>\n\nThere are no feedback messages to display.",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['current_feedbacks'] = feedbacks
    context.user_data['current_feedback_page'] = 0

    await display_feedback_page(update, context, 0)

async def display_feedback_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page_index):
    feedbacks = context.user_data.get('current_feedbacks', [])

    if not feedbacks:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "📭 <b>No Feedbacks</b>\n\nNo feedbacks available.",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "📭 <b>No Feedbacks</b>\n\nNo feedbacks available.",
                parse_mode=ParseMode.HTML
            )
        return

    if page_index < 0:
        page_index = 0
    if page_index >= len(feedbacks):
        page_index = len(feedbacks) - 1

    context.user_data['current_feedback_page'] = page_index
    feedback = feedbacks[page_index]

    timestamp = feedback.get('timestamp', '')
    try:
        if 'T' in timestamp:
            dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            display_time = dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            display_time = timestamp
    except:
        display_time = timestamp

    status_emoji = {
        'new': '🆕',
        'read': '📖',
        'replied': '✅'
    }.get(feedback.get('status', 'new'), '📝')

    status_text = {
        'new': 'New',
        'read': 'Read',
        'replied': 'Replied'
    }.get(feedback.get('status', 'new'), 'Unknown')

    replied_status = "✅ Replied" if feedback.get('replied') else "⏳ Pending Reply"

    # Build a clickable user link using user ID and username
    fb_user_id = feedback.get('user_id', '')
    fb_username = feedback.get('username', '')
    user_link = format_user_link(str(fb_user_id), fb_username) if fb_user_id else format_username_display(fb_username)
    message = (
        f"{status_emoji} <b>Feedback #{page_index + 1}/{len(feedbacks)}</b> • {status_text}\n\n"
        f"👤 <b>User:</b> {html.escape(feedback.get('name', 'Unknown'))} {user_link}\n"
        f"🆔 <b>ID:</b> <code>{feedback.get('user_id', 'Unknown')}</code>\n"
        f"📱 <b>Service:</b> {feedback.get('service_key', 'general')}\n"
        f"⏰ <b>Date:</b> {display_time}\n"
        f"📊 <b>Status:</b> {replied_status}\n\n"
        f"💬 <b>Message:</b>\n<code>{html.escape(feedback.get('feedback', 'No message'))}</code>\n"
    )

    if feedback.get('replied') and feedback.get('reply_message'):
        reply_time = feedback.get('replied_at', '')
        try:
            if 'T' in reply_time:
                dt = datetime.datetime.fromisoformat(reply_time.replace('Z', '+00:00'))
                display_reply_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                display_reply_time = reply_time
        except:
            display_reply_time = reply_time

        message += f"\n📨 <b>Admin Reply:</b>\n<code>{html.escape(feedback.get('reply_message'))}</code>\n"
        if feedback.get('replied_by'):
            message += f"👤 <b>Replied by:</b> {html.escape(feedback.get('replied_by'))}\n"
        message += f"⏰ <b>Reply date:</b> {display_reply_time}\n"

    keyboard = []

    nav_buttons = []
    if page_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous",
                                              callback_data=f"fb_nav_{page_index - 1}"))

    nav_buttons.append(InlineKeyboardButton(f"📄 {page_index + 1}/{len(feedbacks)}",
                                          callback_data="fb_count"))

    if page_index < len(feedbacks) - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️",
                                              callback_data=f"fb_nav_{page_index + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    action_buttons = []
    if not feedback.get('replied'):
        action_buttons.append(InlineKeyboardButton("📨 Reply",
                                                 callback_data=f"fb_reply_{feedback['id']}"))

    status_buttons = []
    current_status = feedback.get('status', 'new')
    if current_status != 'read':
        status_buttons.append(InlineKeyboardButton("📖 Mark Read",
                                                 callback_data=f"fb_mark_read_{feedback['id']}"))
    if current_status != 'new':
        status_buttons.append(InlineKeyboardButton("🆕 Mark New",
                                                 callback_data=f"fb_mark_new_{feedback['id']}"))

    if action_buttons:
        keyboard.append(action_buttons)
    if status_buttons:
        keyboard.append(status_buttons)

    keyboard.append([InlineKeyboardButton("🗑️ Delete",
                                        callback_data=f"fb_delete_{feedback['id']}")])

    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel",
                                        callback_data="admin_panel_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            try:
                if update.callback_query:
                    await update.callback_query.message.reply_text(
                        message,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await update.message.reply_text(
                        message,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e2:
                logger.error(f"Error sending feedback message: {e2}")

async def handle_feedback_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith('fb_nav_'):
        try:
            page_index = int(data.replace('fb_nav_', ''))
            await display_feedback_page(update, context, page_index)
        except (ValueError, IndexError) as e:
            logger.error(f"Error in feedback navigation: {e}")
            await query.answer("❌ Navigation error")
    else:
        await query.answer("❌ Unknown navigation command")

async def handle_feedback_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = update.effective_user

    if data.startswith('fb_reply_'):
        feedback_id = data.replace('fb_reply_', '')
        context.user_data['awaiting_feedback_reply'] = feedback_id
        context.user_data['feedback_reply_admin'] = user.username or user.full_name

        await query.edit_message_text(
            "💬 <b>Reply to Feedback</b>\n\nPlease type your reply message:\n\n<i>Type /cancel to cancel.</i>",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith('fb_mark_read_'):
        feedback_id = data.replace('fb_mark_read_', '')
        if update_feedback_status(feedback_id, 'read'):
            await query.answer("✅ Marked as read")
            current_page = context.user_data.get('current_feedback_page', 0)
            await display_feedback_page(update, context, current_page)
        else:
            await query.answer("❌ Error updating status")

    elif data.startswith('fb_mark_new_'):
        feedback_id = data.replace('fb_mark_new_', '')
        if update_feedback_status(feedback_id, 'new'):
            await query.answer("✅ Marked as new")
            current_page = context.user_data.get('current_feedback_page', 0)
            await display_feedback_page(update, context, current_page)
        else:
            await query.answer("❌ Error updating status")

    elif data.startswith('fb_delete_'):
        feedback_id = data.replace('fb_delete_', '')
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"fb_confirm_delete_{feedback_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="fb_cancel_delete")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "⚠️ <b>Confirm Deletion</b>\n\nAre you sure you want to delete this feedback? This action cannot be undone.",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

async def handle_feedback_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith('fb_confirm_delete_'):
        feedback_id = data.replace('fb_confirm_delete_', '')
        success = delete_feedback(feedback_id)

        if success:
            await query.answer("✅ Feedback deleted")
            feedbacks = get_feedbacks_from_file()
            context.user_data['current_feedbacks'] = feedbacks

            if not feedbacks:
                await query.edit_message_text(
                    "✅ <b>Feedback deleted successfully.</b>\n\nNo more feedbacks available.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
            else:
                current_page = context.user_data.get('current_feedback_page', 0)
                if current_page >= len(feedbacks):
                    current_page = len(feedbacks) - 1
                await display_feedback_page(update, context, current_page)
        else:
            await query.answer("❌ Error deleting feedback")

    elif data == 'fb_cancel_delete':
        current_page = context.user_data.get('current_feedback_page', 0)
        await display_feedback_page(update, context, current_page)

async def handle_feedback_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if 'awaiting_feedback_reply' not in context.user_data:
        return

    feedback_id = context.user_data['awaiting_feedback_reply']
    admin_name = context.user_data.get('feedback_reply_admin', 'Admin')
    reply_text = update.message.text

    feedbacks = get_feedbacks_from_file()
    original_feedback = None
    for fb in feedbacks:
        if fb.get('id') == feedback_id:
            original_feedback = fb
            break

    if not original_feedback:
        await safe_send_message(
            user.id,
            "❌ <b>Error:</b> Feedback not found.",
            context,
            parse_mode=ParseMode.HTML
        )
        context.user_data.pop('awaiting_feedback_reply', None)
        context.user_data.pop('feedback_reply_admin', None)
        return ConversationHandler.END

    success = update_feedback_status(feedback_id, 'replied', reply_text, admin_name)

    if success:
        user_id = original_feedback.get('user_id')
        try:
            user_id_int = int(user_id)
            service_name = services.get(original_feedback.get('service_key', 'general'), {}).get('text', 'General')
            reply_message = (
                f"📨 <b>Reply from Admin</b>\n\n"
                f"Regarding your feedback about <b>{service_name}</b>:\n\n"
                f"<code>{html.escape(reply_text)}</code>\n\n"
                f"<i>Thank you for your feedback!</i>"
            )
            await safe_send_message(
                user_id_int,
                reply_message,
                context,
                parse_mode=ParseMode.HTML
            )
            await safe_send_message(
                user.id,
                f"✅ <b>Reply sent successfully</b> to user {user_id}.",
                context,
                parse_mode=ParseMode.HTML
            )
        except (ValueError, Exception) as e:
            logger.error(f"Error sending reply to user {user_id}: {e}")
            await safe_send_message(
                user.id,
                "✅ <b>Reply saved</b> but could not send to user (they may have blocked the bot).",
                context,
                parse_mode=ParseMode.HTML
            )
    else:
        await safe_send_message(
            user.id,
            "❌ <b>Error:</b> Failed to save reply.",
            context,
            parse_mode=ParseMode.HTML
        )

    context.user_data.pop('awaiting_feedback_reply', None)
    context.user_data.pop('feedback_reply_admin', None)

    return ConversationHandler.END

async def cancel_feedback_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting_feedback_reply', None)
    context.user_data.pop('feedback_reply_admin', None)

    await safe_send_message(
        update.effective_chat.id,
        "❌ <b>Feedback reply cancelled.</b>",
        context,
        parse_mode=ParseMode.HTML
    )

# ------------------ LOAD SERVICES ------------------
def load_services():
    global services
    try:
        newly_loaded_count = 0
        default_bg_path_str = get_safe_background() or str(BASE_DIR / bot_config.get(BACKGROUND_IMAGE_PATH_CONFIG_KEY, DEFAULT_BACKGROUND_IMAGE_FILENAME))

        services_data = load_json(SERVICES_FILE)
        for service_key, data in services_data.items():
            display_name = data.get('display_name', service_key)
            category = data.get('category', 'Other')
            backgrounds_rtdb = data.get('backgrounds')

            if backgrounds_rtdb and isinstance(backgrounds_rtdb, list):
                backgrounds_list = backgrounds_rtdb
            else:
                backgrounds_list = [default_bg_path_str]

            service_data = {
                'backgrounds': backgrounds_list,
                'text': display_name,
                'category': category,
                'created_at_csv_orig': data.get('created_at'),
                'from_db': True
            }

            if service_key not in services or not services[service_key].get('from_code', False):
                if service_key not in services:
                    newly_loaded_count += 1
                services[service_key] = service_data

        if newly_loaded_count > 0:
            logger.info(f"Loaded {newly_loaded_count} services from file.")
    except Exception as e:
        logger.error(f"Error loading services: {e}")

def save_services():
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        services_data = {}

        for key, info in services.items():
            if info.get('from_code') or info.get('from_admin_add'):
                category = info.get('category', 'Other')
                created_at_val = info.get('created_at_csv_orig') or now
                backgrounds_list = info.get('backgrounds', [])

                services_data[key] = {
                    'display_name': info.get('text', key),
                    'category': category,
                    'created_at': created_at_val,
                    'backgrounds': backgrounds_list
                }

        save_json(SERVICES_FILE, services_data)
        logger.info("Services have been saved.")
    except Exception as e:
        logger.error(f"Error saving services: {e}")

# ------------------ NEW ADMIN FEATURES ------------------
async def admin_view_all_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view members.",
            parse_mode=ParseMode.HTML
        )
        return

    users_data = load_json(USERS_JSON_FILE)
    if not users_data:
        await query.edit_message_text(
            "📭 <b>No Members</b>\n\nThere are no registered members yet.",
            parse_mode=ParseMode.HTML
        )
        return

    sorted_users = sorted(users_data.items(),
                         key=lambda x: x[1].get('join_date', ''),
                         reverse=True)

    current_page = context.user_data.get('current_members_page', 0)
    users_per_page = 8  # Reduced for more detailed info

    total_pages = (len(sorted_users) + users_per_page - 1) // users_per_page

    if current_page >= total_pages:
        current_page = total_pages - 1
    if current_page < 0:
        current_page = 0

    start_idx = current_page * users_per_page
    end_idx = start_idx + users_per_page
    page_users = sorted_users[start_idx:end_idx]

    message = f"👥 <b>All Members</b> - Page {current_page + 1}/{total_pages}\n\n"

    for i, (user_id, user_info) in enumerate(page_users, start=start_idx + 1):
        username = user_info.get('username', 'No username')
        join_date = user_info.get('join_date', 'Unknown')
        premium_until = user_info.get('premium_until', 'Not premium')
        referrals = user_info.get('total_referrals', 0)
        total_claims = user_info.get('total_claims', 0)
        points = get_user_points(user_id)

        # Get transfer stats
        user_transfers = get_user_transfers(user_id)
        points_sent = sum(int(t.get('amount', 0)) for t in user_transfers if t.get('sender_id') == str(user_id))
        points_received = sum(int(t.get('amount', 0)) for t in user_transfers if t.get('recipient_id') == str(user_id))

        # Get redemption stats
        redemptions = get_user_key_redemptions(user_id)
        premium_redemptions = len([r for r in redemptions if r.get('type') == 'premium'])
        points_redemptions = len([r for r in redemptions if r.get('type') == 'points'])

        premium_status = "💎 Premium" if premium_until else "👤 Standard"

        # Build a clickable link for the user
        if username and username != 'No username':
            user_link = format_user_link(user_id, username)
        else:
            user_link = f"User {user_id}"

        # Get latest redemption code
        latest_redemption = ""
        if redemptions:
            latest = redemptions[0]
            if latest.get('type') == 'premium':
                key_short = latest.get('key', '')[:12] + '...'
                latest_redemption = f"\n   Last Redeem: 💎 {key_short}"
            else:
                key_short = latest.get('key', '')[:12] + '...'
                latest_redemption = f"\n   Last Redeem: 💰 {key_short}"

        message += (
            f"<b>{i}.</b> {user_link}\n"
            f"   Status: {premium_status}\n"
            f"   Points: {points} | Referrals: {referrals} | Claims: {total_claims}\n"
            f"   Transfers: 🔽{points_sent} 🔼{points_received} ({len(user_transfers)}x)\n"
            f"   Redemptions: 💎{premium_redemptions} 💰{points_redemptions}{latest_redemption}\n"
            f"   Joined: {join_date}\n\n"
        )

    keyboard = []

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"members_nav_{current_page - 1}"))

    nav_buttons.append(InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="members_count"))

    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"members_nav_{current_page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔍 User Details", callback_data="admin_view_users_ref_claims"),
        InlineKeyboardButton("📊 Transfer Stats", callback_data="admin_transfer_stats")
    ])

    keyboard.append([
        InlineKeyboardButton("🔑 Redemption Stats", callback_data="admin_redemption_stats"),
        InlineKeyboardButton("📊 Export Members", callback_data="admin_export_members")
    ])

    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error editing message: {e}")

# Update the admin_view_users_ref_claims function to include transfer and redemption info
async def admin_view_users_ref_claims(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display a paginated list of users along with their referral, claim, transfer,
    and redemption statistics.
    """
    query = update.callback_query
    try:
        if query:
            await query.answer()
    except Exception:
        pass

    admin_user = update.effective_user
    if not admin_user or not is_admin(admin_user.id):
        if query and query.message:
            await query.message.edit_text(
                "❌ <b>Access Denied</b>\n\nYou don't have permission to view this.",
                parse_mode=ParseMode.HTML
            )
        return

    current_page = context.user_data.get('users_ref_claims_page', 0)
    items_per_page = 6  # Reduced for more detailed info

    users_data = load_json(USERS_JSON_FILE)
    claims_data = load_json(CLAIMED_ACCOUNTS_FILE)

    user_stats = []
    for uid_str, udata in users_data.items():
        username = udata.get('username') or f"user{uid_str}"

        # Referral names
        referee_names = get_user_referee_usernames(uid_str)
        ref_count = len(referee_names)

        # Claim count
        claim_list = claims_data.get(uid_str, [])
        claim_count = len(claim_list)

        # Latest claim info
        latest_claim_time = None
        latest_service = None
        if claim_list:
            try:
                sorted_claims = sorted(
                    claim_list,
                    key=lambda x: x.get('claim_date') or x.get('timestamp') or '',
                    reverse=True
                )
            except Exception:
                sorted_claims = claim_list
            latest = sorted_claims[0]
            date_str = latest.get('claim_date') or latest.get('timestamp')
            if date_str:
                try:
                    latest_dt = datetime.datetime.fromisoformat(date_str)
                except Exception:
                    try:
                        latest_dt = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        latest_dt = None
                if latest_dt:
                    latest_claim_time = latest_dt.strftime('%Y-%m-%d %H:%M')
            latest_service = latest.get('service_key')

        # Transfer stats
        user_transfers = get_user_transfers(uid_str)
        points_sent = sum(int(t.get('amount', 0)) for t in user_transfers if t.get('sender_id') == uid_str)
        points_received = sum(int(t.get('amount', 0)) for t in user_transfers if t.get('recipient_id') == uid_str)
        total_transfers = len(user_transfers)

        # Redemption stats
        redemptions = get_user_key_redemptions(uid_str)
        premium_redemptions = len([r for r in redemptions if r.get('type') == 'premium'])
        points_redemptions = len([r for r in redemptions if r.get('type') == 'points'])
        total_redemptions = len(redemptions)

        # Points
        points = get_user_points(uid_str)

        user_stats.append({
            'user_id': uid_str,
            'username': username,
            'points': points,
            'ref_count': ref_count,
            'ref_names': referee_names,
            'claim_count': claim_count,
            'latest_time': latest_claim_time,
            'latest_service': latest_service,
            'points_sent': points_sent,
            'points_received': points_received,
            'total_transfers': total_transfers,
            'premium_redemptions': premium_redemptions,
            'points_redemptions': points_redemptions,
            'total_redemptions': total_redemptions
        })

    # Sort by total activity (claims + transfers + redemptions)
    user_stats.sort(key=lambda x: (
        x['claim_count'] +
        x['total_transfers'] +
        x['total_redemptions'] +
        x['ref_count']
    ), reverse=True)

    total_pages = (len(user_stats) + items_per_page - 1) // items_per_page
    if current_page >= total_pages:
        current_page = max(0, total_pages - 1)
        context.user_data['users_ref_claims_page'] = current_page

    start_idx = current_page * items_per_page
    end_idx = start_idx + items_per_page
    page_stats = user_stats[start_idx:end_idx]

    msg_lines = []
    msg_lines.append(f"👥 <b>Users Activity Dashboard</b> - Page {current_page + 1}/{total_pages}\n")

    for i, stat in enumerate(page_stats, start=start_idx + 1):
        uid = stat['user_id']
        uname = stat['username']
        user_link = format_user_link(uid, uname)

        line = f"<b>{i}.</b> {user_link}\n"
        line += f"   Points: {stat['points']} | Ref: {stat['ref_count']} | Claims: {stat['claim_count']}\n"
        line += f"   Transfers: 🔽{stat['points_sent']} 🔼{stat['points_received']} ({stat['total_transfers']}x)\n"
        line += f"   Redemptions: 💎{stat['premium_redemptions']} 💰{stat['points_redemptions']} ({stat['total_redemptions']}x)\n"

        # Show latest redemption code
        redemptions = get_user_key_redemptions(uid)
        if redemptions:
            latest_redeem = redemptions[0]
            key_short = latest_redeem.get('key', '')[:12] + '...'
            if latest_redeem.get('type') == 'premium':
                line += f"   Last Redeem: 💎 {key_short}\n"
            else:
                line += f"   Last Redeem: 💰 {key_short}\n"

        if stat['claim_count'] > 0 and stat['latest_time']:
            latest_info = f"{stat['latest_time']}"
            if stat['latest_service']:
                latest_info += f" on {stat['latest_service']}"
            line += f"   Last Claim: {latest_info}\n"

        msg_lines.append(line)

    message_text = "\n".join(msg_lines)

    nav_buttons = []
    if total_pages > 1:
        if current_page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"users_ref_claims_nav_{current_page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="users_ref_claims_count"))
        if current_page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"users_ref_claims_nav_{current_page + 1}"))

    keyboard = []

    # Add a details button for each user in the current page
    for stat in page_stats:
        username_display = format_username_display(stat['username'])
        keyboard.append([
            InlineKeyboardButton(
                f"🔍 Details: {username_display}",
                callback_data=f"admin_user_details_{stat['user_id']}"
            )
        ])

    # Navigation row
    if nav_buttons:
        keyboard.append(nav_buttons)

    # Additional action buttons
    keyboard.append([
        InlineKeyboardButton("📊 Transfer Stats", callback_data="admin_transfer_stats"),
        InlineKeyboardButton("🔑 Redemption Stats", callback_data="admin_redemption_stats")
    ])

    # Back button
    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            chat_id = update.effective_chat.id
            await safe_send_message(chat_id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Error in admin_view_users_ref_claims: {e}")


async def admin_view_claimed_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view claimed accounts.",
            parse_mode=ParseMode.HTML
        )
        return

    all_claims = get_all_claims(limit=100)

    if not all_claims:
        await query.edit_message_text(
            "📭 <b>No Claimed Accounts</b>\n\nNo accounts have been claimed yet.",
            parse_mode=ParseMode.HTML
        )
        return

    user_claims = {}
    for claim in all_claims:
        user_id = claim.get('user_id')
        if user_id not in user_claims:
            user_claims[user_id] = []
        user_claims[user_id].append(claim)

    message = "📦 <b>Claimed Accounts Overview</b>\n\n"

    for user_id, claims in list(user_claims.items())[:20]:
        user_info = load_json(USERS_JSON_FILE).get(user_id, {})
        username = user_info.get('username', f"user{user_id}")
        claim_count = len(claims)

        services_claimed = set(claim.get('service_key', 'Unknown') for claim in claims)

        # Build a clickable link for the user using user ID and username
        user_link = format_user_link(user_id, username)
        message += (
            f"👤 {user_link}\n"
            f"   Total Claims: {claim_count}\n"
            f"   Services: {', '.join(services_claimed)}\n\n"
        )

    total_claims = len(all_claims)
    unique_users = len(user_claims)

    message += f"<b>Summary:</b>\n"
    message += f"• Total Claims: {total_claims}\n"
    message += f"• Unique Users: {unique_users}\n"
    message += f"• Average per User: {total_claims/unique_users:.1f}\n\n"

    keyboard = [
        [
            InlineKeyboardButton("👤 View by User", callback_data="admin_claims_by_user"),
            InlineKeyboardButton("📱 View by Service", callback_data="admin_claims_by_service")
        ],
        [
            InlineKeyboardButton("📊 Recent Claims", callback_data="admin_recent_claims"),
            InlineKeyboardButton("📈 Statistics", callback_data="admin_claims_stats")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_view_claims_by_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_page = context.user_data.get('claims_user_page', 0)
    users_per_page = 5

    all_claims = get_all_claims(limit=500)
    user_claims = {}

    for claim in all_claims:
        user_id = claim.get('user_id')
        if user_id not in user_claims:
            user_claims[user_id] = []
        user_claims[user_id].append(claim)

    sorted_users = sorted(user_claims.items(),
                         key=lambda x: len(x[1]),
                         reverse=True)

    total_pages = (len(sorted_users) + users_per_page - 1) // users_per_page

    if current_page >= total_pages:
        current_page = total_pages - 1
    if current_page < 0:
        current_page = 0

    start_idx = current_page * users_per_page
    end_idx = start_idx + users_per_page
    page_users = sorted_users[start_idx:end_idx]

    message = f"👤 <b>Claims by User</b> - Page {current_page + 1}/{total_pages}\n\n"

    for i, (user_id, claims) in enumerate(page_users, start=start_idx + 1):
        user_info = load_json(USERS_JSON_FILE).get(user_id, {})
        username = user_info.get('username', f"user{user_id}")
        claim_count = len(claims)

        recent_claim = max(claims, key=lambda x: x.get('timestamp', ''))
        recent_service = recent_claim.get('service_key', 'Unknown')
        recent_date = recent_claim.get('timestamp', 'Unknown')

        # Build a clickable user link if possible
        user_link = format_user_link(user_id, username)
        message += (
            f"<b>{i}.</b> {user_link}\n"
            f"   Total Claims: {claim_count}\n"
            f"   Recent: {recent_service} on {recent_date[:10]}\n\n"
        )

    keyboard = []

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"claims_user_nav_{current_page - 1}"))

    nav_buttons.append(InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="claims_user_count"))

    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"claims_user_nav_{current_page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔍 View User Claims", callback_data="admin_view_specific_user_claims"),
        InlineKeyboardButton("📱 Back to Overview", callback_data="admin_view_claimed_accounts")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_view_specific_user_claims(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Prompt admin to enter a user ID or username to view their detailed claim history.
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view user claims.",
            parse_mode=ParseMode.HTML
        )
        return

    # Set up context to wait for user input
    context.user_data['awaiting_admin_input'] = 'view_specific_user_claims'
    context.user_data['return_to_menu'] = 'admin_view_claimed_accounts'

    await query.edit_message_text(
        "🔍 <b>View User Claims</b>\n\n"
        "Enter the user's ID or username to view their claim history:\n\n"
        "<i>Examples:</i>\n"
        "<code>123456789</code> (User ID)\n"
        "<code>@username</code> (Username)\n\n"
        "Type /cancel_admin to cancel.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Cancel", callback_data="admin_view_claimed_accounts")
        ]])
    )

async def admin_view_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view referral stats.",
            parse_mode=ParseMode.HTML
        )
        return

    stats = get_referral_statistics()

    if not stats:
        await query.edit_message_text(
            "📊 <b>No Referral Data</b>\n\nNo referrals have been made yet.",
            parse_mode=ParseMode.HTML
        )
        return

    message = "📈 <b>Referral Statistics</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Total Referrals: {stats['total_referrals']}\n"
    message += f"• Active Referrals: {stats['active_referrals']}\n\n"

    message += f"<b>Top Referrers:</b>\n"
    for i, referrer in enumerate(stats['top_referrers'][:10], 1):
        # Use helper to format username (avoid '@' on fallback usernames)
        message += f"{i}. {format_username_display(referrer['username'])}: {referrer['referral_count']} referrals\n"

    message += f"\n<b>Recent Referrals:</b>\n"
    for ref in stats['recent_referrals'][:5]:
        # Format both referrer and referee names properly
        message += f"• {format_username_display(ref['referrer'])} → {format_username_display(ref['referee'])} ({ref['date'][:10]})\n"

    keyboard = [
        [
            InlineKeyboardButton("📋 Export Stats", callback_data="admin_export_referral_stats"),
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_referral_stats")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_transfer_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display comprehensive transfer statistics for admins.
    Shows total transfers, top senders/receivers, and recent transfer activity.
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view transfer stats.",
            parse_mode=ParseMode.HTML
        )
        return

    # Get all transfers
    all_transfers = get_all_transfers(limit=1000)

    if not all_transfers:
        await query.edit_message_text(
            "📊 <b>No Transfer Data</b>\n\nNo point transfers have been made yet.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")
            ]])
        )
        return

    # Calculate statistics
    total_transfers = len(all_transfers)
    total_points_transferred = sum(int(t.get('amount', 0)) for t in all_transfers)

    # Track sender and receiver statistics
    sender_stats = {}
    receiver_stats = {}

    for transfer in all_transfers:
        sender_id = transfer.get('sender_id')
        recipient_id = transfer.get('recipient_id')
        amount = int(transfer.get('amount', 0))

        # Sender stats
        if sender_id:
            if sender_id not in sender_stats:
                sender_stats[sender_id] = {'username': transfer.get('sender_username', f'user{sender_id}'), 'total': 0, 'count': 0}
            sender_stats[sender_id]['total'] += amount
            sender_stats[sender_id]['count'] += 1

        # Receiver stats
        if recipient_id:
            if recipient_id not in receiver_stats:
                receiver_stats[recipient_id] = {'username': transfer.get('recipient_username', f'user{recipient_id}'), 'total': 0, 'count': 0}
            receiver_stats[recipient_id]['total'] += amount
            receiver_stats[recipient_id]['count'] += 1

    # Get top senders and receivers
    top_senders = sorted(sender_stats.items(), key=lambda x: x[1]['total'], reverse=True)[:5]
    top_receivers = sorted(receiver_stats.items(), key=lambda x: x[1]['total'], reverse=True)[:5]

    # Build message
    message = "📊 <b>Transfer Statistics</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Total Transfers: {total_transfers}\n"
    message += f"• Total Points Transferred: {total_points_transferred:,}\n"
    message += f"• Average per Transfer: {total_points_transferred // total_transfers if total_transfers > 0 else 0}\n\n"

    message += f"<b>🔽 Top Senders:</b>\n"
    for i, (user_id, stats) in enumerate(top_senders, 1):
        username_display = format_username_display(stats['username'])
        message += f"{i}. {username_display}: {stats['total']:,} pts ({stats['count']}x)\n"

    message += f"\n<b>🔼 Top Receivers:</b>\n"
    for i, (user_id, stats) in enumerate(top_receivers, 1):
        username_display = format_username_display(stats['username'])
        message += f"{i}. {username_display}: {stats['total']:,} pts ({stats['count']}x)\n"

    message += f"\n<b>🕒 Recent Transfers:</b>\n"
    for transfer in all_transfers[:5]:
        sender = format_username_display(transfer.get('sender_username', 'Unknown'))
        recipient = format_username_display(transfer.get('recipient_username', 'Unknown'))
        amount = transfer.get('amount', 0)
        timestamp = transfer.get('timestamp', '')[:10]
        message += f"• {sender} → {recipient}: {amount} pts ({timestamp})\n"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_transfer_stats"),
            InlineKeyboardButton("👥 View Members", callback_data="admin_view_all_members_interactive")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_redemption_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display comprehensive redemption statistics for admins.
    Shows premium and point key redemptions, top redeemers, and recent activity.
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view redemption stats.",
            parse_mode=ParseMode.HTML
        )
        return

    # Get all keys
    keys_data = load_json(KEYS_FILE)
    point_keys_data = load_json(POINT_KEYS_FILE)

    # Count redemptions
    premium_redemptions = []
    point_redemptions = []

    for key, key_info in keys_data.items():
        if key_info.get('status') == 'used':
            premium_redemptions.append({
                'key': key,
                'user_id': key_info.get('used_by'),
                'type': key_info.get('type', 'lifetime'),
                'used_at': key_info.get('used_at', '')
            })

    for key, key_info in point_keys_data.items():
        if key_info.get('status') == 'used':
            point_redemptions.append({
                'key': key,
                'user_id': key_info.get('used_by'),
                'points': key_info.get('points', 0),
                'used_at': key_info.get('used_at', '')
            })

    total_premium = len(premium_redemptions)
    total_points_keys = len(point_redemptions)
    total_points_redeemed = sum(r.get('points', 0) for r in point_redemptions)

    # Track user redemption stats
    user_redemption_stats = {}

    for redemption in premium_redemptions:
        user_id = redemption.get('user_id')
        if user_id:
            if user_id not in user_redemption_stats:
                users_data = load_json(USERS_JSON_FILE)
                username = users_data.get(user_id, {}).get('username', f'user{user_id}')
                user_redemption_stats[user_id] = {'username': username, 'premium': 0, 'points': 0, 'total_points': 0}
            user_redemption_stats[user_id]['premium'] += 1

    for redemption in point_redemptions:
        user_id = redemption.get('user_id')
        if user_id:
            if user_id not in user_redemption_stats:
                users_data = load_json(USERS_JSON_FILE)
                username = users_data.get(user_id, {}).get('username', f'user{user_id}')
                user_redemption_stats[user_id] = {'username': username, 'premium': 0, 'points': 0, 'total_points': 0}
            user_redemption_stats[user_id]['points'] += 1
            user_redemption_stats[user_id]['total_points'] += redemption.get('points', 0)

    # Get top redeemers
    top_redeemers = sorted(
        user_redemption_stats.items(),
        key=lambda x: x[1]['premium'] + x[1]['points'],
        reverse=True
    )[:5]

    # Build message
    message = "🔑 <b>Redemption Statistics</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Premium Keys Redeemed: {total_premium}\n"
    message += f"• Point Keys Redeemed: {total_points_keys}\n"
    message += f"• Total Points from Keys: {total_points_redeemed:,}\n"
    message += f"• Total Redemptions: {total_premium + total_points_keys}\n\n"

    if top_redeemers:
        message += f"<b>🏆 Top Redeemers:</b>\n"
        for i, (user_id, stats) in enumerate(top_redeemers, 1):
            username_display = format_username_display(stats['username'])
            message += f"{i}. {username_display}: 💎{stats['premium']} 💰{stats['points']} ({stats['total_points']:,} pts)\n"

    # Recent redemptions
    all_redemptions = []
    for r in premium_redemptions:
        r['redemption_type'] = 'premium'
        all_redemptions.append(r)
    for r in point_redemptions:
        r['redemption_type'] = 'points'
        all_redemptions.append(r)

    all_redemptions.sort(key=lambda x: x.get('used_at', ''), reverse=True)

    if all_redemptions:
        message += f"\n<b>🕒 Recent Redemptions:</b>\n"
        for redemption in all_redemptions[:5]:
            user_id = redemption.get('user_id')
            users_data = load_json(USERS_JSON_FILE)
            username = users_data.get(user_id, {}).get('username', f'user{user_id}')
            username_display = format_username_display(username)

            key_short = redemption.get('key', '')[:12] + '...'
            timestamp = redemption.get('used_at', '')[:10]

            if redemption.get('redemption_type') == 'premium':
                key_type = redemption.get('type', 'lifetime')
                message += f"• {username_display}: 💎 {key_type} - {key_short} ({timestamp})\n"
            else:
                points = redemption.get('points', 0)
                message += f"• {username_display}: 💰 {points} pts - {key_short} ({timestamp})\n"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_redemption_stats"),
            InlineKeyboardButton("👥 View Members", callback_data="admin_view_all_members_interactive")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_live_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display real-time stock counts for all services.
    Shows each service with its current item count in format: Service Name (X items)
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view live stocks.",
            parse_mode=ParseMode.HTML
        )
        return

    # Get all services and their stock counts
    service_stocks = []
    total_items = 0
    low_stock_count = 0
    out_of_stock_count = 0
    low_stock_threshold = bot_config.get("low_stock_threshold", 5)

    for service_key, service_info in services.items():
        stock = get_service_stock(service_key)
        display_name = service_info.get('text', service_key)
        category = service_info.get('category', 'Other')

        # Track statistics
        total_items += stock
        if stock == 0:
            out_of_stock_count += 1
        elif stock <= low_stock_threshold:
            low_stock_count += 1

        service_stocks.append({
            'key': service_key,
            'name': display_name,
            'stock': stock,
            'category': category
        })

    # Sort by stock count (lowest first to highlight issues)
    service_stocks.sort(key=lambda x: (x['stock'], x['name']))

    # Build message
    message = "📊 <b>Live Stock Inventory</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Total Services: {len(service_stocks)}\n"
    message += f"• Total Items: {total_items:,}\n"
    message += f"• Out of Stock: {out_of_stock_count}\n"
    message += f"• Low Stock (≤{low_stock_threshold}): {low_stock_count}\n\n"

    # Group by category
    categories = {}
    for item in service_stocks:
        cat = item['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    # Display by category
    for category, items in sorted(categories.items()):
        message += f"<b>📱 {category}:</b>\n"
        for item in items:
            stock = item['stock']
            name = item['name']

            # Add status emoji
            if stock == 0:
                status_emoji = "❌"
            elif stock <= low_stock_threshold:
                status_emoji = "⚠️"
            else:
                status_emoji = "✅"

            message += f"{status_emoji} {name}: <b>{stock}</b> item{'s' if stock != 1 else ''}\n"
        message += "\n"

    # Add legend
    message += "<i>Legend: ✅ Good Stock | ⚠️ Low Stock | ❌ Out of Stock</i>"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_live_stocks"),
            InlineKeyboardButton("📦 Services Mgt", callback_data="admin_services_menu")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def user_live_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display real-time stock counts for all services (user-facing version).
    Shows simplified view with service availability.
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user:
        return

    # Get all services and their stock counts
    service_stocks = []
    total_items = 0
    available_services = 0
    low_stock_threshold = bot_config.get("low_stock_threshold", 5)

    for service_key, service_info in services.items():
        stock = get_service_stock(service_key)
        display_name = service_info.get('text', service_key)
        category = service_info.get('category', 'Other')

        # Track statistics
        total_items += stock
        if stock > 0:
            available_services += 1

        service_stocks.append({
            'key': service_key,
            'name': display_name,
            'stock': stock,
            'category': category
        })

    # Sort by category then name
    service_stocks.sort(key=lambda x: (x['category'], x['name']))

    # Build message
    message = "📊 <b>Live Stock Availability</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Total Services: {len(service_stocks)}\n"
    message += f"• Available Now: {available_services}\n"
    message += f"• Total Items: {total_items:,}\n\n"

    # Group by category
    categories = {}
    for item in service_stocks:
        cat = item['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    # Display by category
    for category, items in sorted(categories.items()):
        message += f"<b>📱 {category}:</b>\n"
        for item in items:
            stock = item['stock']
            name = item['name']

            # Add status emoji
            if stock == 0:
                status_emoji = "❌"
                status_text = "Out of Stock"
            elif stock <= low_stock_threshold:
                status_emoji = "⚠️"
                status_text = f"{stock} left"
            else:
                status_emoji = "✅"
                status_text = "Available"

            message += f"{status_emoji} {name}: <b>{status_text}</b>\n"
        message += "\n"

    # Add helpful note
    message += "<i>✅ = In Stock | ⚠️ = Low Stock | ❌ = Out of Stock</i>\n\n"
    message += "🔄 <i>Stocks update in real-time. Refresh to see latest availability.</i>"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="user_live_stocks"),
            InlineKeyboardButton("🎁 Get Services", callback_data="select_category")
        ],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

# ------------------ ADMIN USERS REFERRAL & CLAIMS VIEW ------------------


async def admin_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show full details for a specific user, including all referrals and the complete claim history.
    Triggered by callback data of the form `admin_user_details_<user_id>`.
    """
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass
    # Ensure caller is an admin
    caller = update.effective_user
    if not caller or not is_admin(caller.id):
        if query and query.message:
            await query.message.edit_text(
                "❌ <b>Access Denied</b>\n\nYou don't have permission to view this.",
                parse_mode=ParseMode.HTML
            )
        return
    data = query.data if query else ''
    # Extract user ID from callback data (assumes format admin_user_details_<id>)
    try:
        user_id = data.split('_', 3)[3]
    except Exception:
        user_id = None
    if not user_id:
        await safe_send_message(caller.id, "❌ Invalid user reference.", context, parse_mode=ParseMode.HTML)
        return
    users_data = load_json(USERS_JSON_FILE)
    udata = users_data.get(str(user_id), {})
    username = udata.get('username') or f"user{user_id}"
    join_date = udata.get('join_date', 'Unknown')
    points = get_user_points(user_id)
    is_premium, premium_until = check_user_premium_status(user_id)
    referral_names = get_user_referee_usernames(user_id)
    referral_count = len(referral_names)
    # Build referrals display
    if referral_names:
        # Format referral names using helper (avoid '@' on fallback usernames)
        formatted_refs = [format_username_display(name) for name in referral_names]
        referrals_text = ", ".join(formatted_refs)
    else:
        referrals_text = "None"
    # Gather claims
    claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
    user_claims = claims_data.get(str(user_id), [])
    # Sort claims by date descending
    try:
        sorted_claims = sorted(
            user_claims,
            key=lambda x: x.get('claim_date') or x.get('timestamp') or '',
            reverse=True
        )
    except Exception:
        sorted_claims = user_claims
    # Build claims text
    if sorted_claims:
        claim_lines = []
        for claim in sorted_claims:
            service_key = claim.get('service_key', 'unknown')
            date_str = claim.get('claim_date') or claim.get('timestamp') or ''
            # Shorten timestamp for display
            if date_str:
                try:
                    dt = datetime.datetime.fromisoformat(date_str)
                except Exception:
                    try:
                        dt = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        dt = None
                if dt:
                    date_display = dt.strftime('%Y-%m-%d %H:%M')
                else:
                    date_display = date_str[:16]
            else:
                date_display = 'Unknown'
            claim_lines.append(f"• {service_key} on {date_display}")
        claims_text = "\n".join(claim_lines)
    else:
        claims_text = "No claims recorded."
    total_claims = len(user_claims)
    message = (
        f"👤 <b>User Details</b>\n\n"
        f"<b>Username:</b> {format_username_display(username)}\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Joined:</b> {join_date}\n"
        f"<b>Points:</b> {points}\n"
        f"<b>Premium Status:</b> {'✅ Yes' if is_premium else '❌ No'}\n"
        f"<b>Premium Until:</b> {premium_until if premium_until else 'N/A'}\n"
        f"<b>Total Referrals:</b> {referral_count}\n"
        f"<b>Referees:</b> {referrals_text}\n\n"
        f"<b>Total Claims:</b> {total_claims}\n"
        f"<b>Claim History:</b>\n{claims_text}"
    )
    # Build buttons
    back_page = context.user_data.get('users_ref_claims_page', 0)
    back_callback = f"users_ref_claims_nav_{back_page}"
    buttons = []
    # Admin actions
    buttons.append([InlineKeyboardButton("💰 Adjust Points", callback_data=f"admin_points_adjust_{user_id}")])
    buttons.append([InlineKeyboardButton("💎 Grant Premium", callback_data=f"admin_grant_premium_{user_id}")])
    # Navigation buttons
    buttons.append([InlineKeyboardButton("⬅️ Back to Users List", callback_data=back_callback)])
    buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])
    reply_markup = InlineKeyboardMarkup(buttons)
    # Send or edit message
    try:
        if query and query.message:
            await query.message.edit_text(message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            chat_id = update.effective_chat.id
            await safe_send_message(chat_id, message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Error sending user details: {e}")

async def admin_view_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display the current bot status (online/offline) to the admin.
    """
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass
    # Ensure caller is admin
    caller = update.effective_user
    if not caller or not is_admin(caller.id):
        if query and query.message:
            await query.message.edit_text(
                "❌ <b>Access Denied</b>\n\nYou don't have permission to view this.",
                parse_mode=ParseMode.HTML
            )
        return
    # Determine current status
    current_status = get_bot_status()
    status_text = "🟢 ONLINE" if current_status else "🔴 OFFLINE"
    message = (
        f"🤖 <b>Bot Status</b>\n\n"
        f"The bot is currently: {status_text}\n\n"
        f"Only admins can enable or disable the bot from the Bot Status Management page."
    )
    # Build buttons: back to bot status management, back to admin panel
    buttons = [
        [InlineKeyboardButton("⬅️ Back to Bot Status", callback_data="admin_manage_bot_status")],
        [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_panel_main")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    try:
        if query and query.message:
            await query.message.edit_text(message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            chat_id = update.effective_chat.id
            await safe_send_message(chat_id, message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Error displaying bot status: {e}")

async def admin_view_service_claims(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display the full claim history for a specific service. The service key and
    current page are stored in context.user_data. Claims are shown in
    chronological order (most recent first) with pagination. Each entry
    displays the claiming user's username, the date/time of the claim, and a
    preview of the claimed account credentials (truncated for readability).

    Expected context variables:
    - 'service_claims_service': the key of the service to display
    - 'service_claims_page': the page index to display

    Navigation callbacks use the format 'service_claims_nav_<service_key>_<page>'.
    """
    query = update.callback_query
    # Answer query to remove loading spinner
    if query:
        try:
            await query.answer()
        except Exception:
            pass

    # Verify that the caller is an admin
    caller = update.effective_user
    if not caller or not is_admin(caller.id):
        if query and query.message:
            await query.message.edit_text(
                "❌ <b>Access Denied</b>\n\nYou don't have permission to view this.",
                parse_mode=ParseMode.HTML
            )
        return

    # Determine service key and page from context
    service_key = context.user_data.get('service_claims_service')
    page = context.user_data.get('service_claims_page', 0)
    # If the callback data includes a service key, override context
    if query and query.data:
        data = query.data
        # Pattern: admin_service_claims_<service_key> or service_claims_nav_<service_key>_<page>
        if data.startswith('admin_service_claims_'):
            # Extract the service key (everything after the prefix)
            service_key = data.replace('admin_service_claims_', '', 1)
            context.user_data['service_claims_service'] = service_key
            page = 0
            context.user_data['service_claims_page'] = page
        elif data.startswith('service_claims_nav_'):
            # Remove prefix and then split from the right to get service_key and page
            nav_data = data.replace('service_claims_nav_', '', 1)
            # rsplit only once to separate page number
            try:
                svc_key, page_str = nav_data.rsplit('_', 1)
            except ValueError:
                svc_key, page_str = nav_data, '0'
            service_key = svc_key
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            context.user_data['service_claims_service'] = service_key
            context.user_data['service_claims_page'] = page

    if not service_key:
        # No service selected, prompt admin
        if query and query.message:
            await query.message.edit_text(
                "❌ <b>No Service Selected</b>\n\nPlease return and choose a service from the list first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="admin_view_specific_service_claims")],
                    [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
                ]),
                parse_mode=ParseMode.HTML
            )
        return

    # Retrieve claims for this service
    # Use a high limit to capture full history
    try:
        all_claims = get_claims_by_service(service_key, limit=10000)
    except Exception as e:
        logger.error(f"Error loading claims for service {service_key}: {e}")
        all_claims = []
    total_claims = len(all_claims)

    if total_claims == 0:
        message_text = (
            f"📭 <b>No Claims for {services.get(service_key, {}).get('text', service_key)}</b>\n\n"
            f"This service has no claim history."
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Service List", callback_data="admin_view_specific_service_claims")],
            [InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_claims_by_service")],
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
        ])
        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            chat_id = update.effective_chat.id
            await safe_send_message(chat_id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    # Pagination parameters
    items_per_page = 8
    total_pages = (total_claims + items_per_page - 1) // items_per_page
    # Clamp page
    if page >= total_pages:
        page = max(0, total_pages - 1)
        context.user_data['service_claims_page'] = page
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_claims = all_claims[start_idx:end_idx]

    # Build message text
    svc_name = services.get(service_key, {}).get('text', service_key)
    message_lines = [f"📜 <b>{svc_name} Claims History</b> - Page {page + 1}/{total_pages}\n"]
    for idx, claim in enumerate(page_claims, start=start_idx + 1):
        # Retrieve username and user ID
        username = claim.get('username') or ''
        user_id = claim.get('user_id')
        # Build clickable user link. If no user ID is present, fall back to plain text.
        if user_id:
            user_link = format_user_link(user_id, username)
        else:
            # Fallback when user_id is missing: format the username without making it clickable
            user_link = format_username_display(username) if username else "Unknown user"
        # Determine date/time
        date_str = claim.get('timestamp') or claim.get('claim_date') or ''
        if date_str:
            # Attempt to parse into datetime
            try:
                dt = datetime.datetime.fromisoformat(date_str)
            except Exception:
                try:
                    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    dt = None
            date_display = dt.strftime('%Y-%m-%d %H:%M') if dt else date_str[:16]
        else:
            date_display = 'Unknown'
        # Account preview
        account = claim.get('account', '') or claim.get('acc', '')
        acc_preview = account
        if account and len(account) > 50:
            acc_preview = account[:50] + '...'
        # Compose entry line
        line = f"<b>{idx}.</b> {user_link} on {date_display}\n"
        if acc_preview:
            # Escape HTML in account preview
            import html
            acc_display = html.escape(acc_preview)
            line += f"   <code>{acc_display}</code>\n"
        message_lines.append(line)
    message_text = "\n".join(message_lines)

    # Build navigation buttons
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"service_claims_nav_{service_key}_{page - 1}"
            ))
        # Current page indicator
        nav_buttons.append(InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data="claims_service_count"
        ))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"service_claims_nav_{service_key}_{page + 1}"
            ))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    # Back navigation
    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Service List", callback_data="admin_view_specific_service_claims"),
        InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_claims_by_service")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send or edit message
    try:
        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            chat_id = update.effective_chat.id
            await safe_send_message(chat_id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Error displaying service claims: {e}")

async def admin_manage_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to manage bot status.",
            parse_mode=ParseMode.HTML
        )
        return

    current_status = get_bot_status()
    status_text = "🟢 ONLINE" if current_status else "🔴 OFFLINE"

    message = (
        f"⚙️ <b>Bot Status Management</b>\n\n"
        f"Current Status: {status_text}\n\n"
        f"<b>Options:</b>\n"
        f"• Enable Bot: Allows all users to use the bot\n"
        f"• Disable Bot: Only admins can use the bot\n\n"
        f"<i>When disabled, users will see a maintenance message.</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🟢 Enable Bot", callback_data="admin_enable_bot"),
            InlineKeyboardButton("🔴 Disable Bot", callback_data="admin_disable_bot")
        ],
        [
            InlineKeyboardButton("📊 View Status", callback_data="admin_view_bot_status"),
            InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_panel_main")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_enable_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if set_bot_status(True):
        await query.edit_message_text(
            "✅ <b>Bot Enabled Successfully!</b>\n\n"
            "The bot is now online and available to all users.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            "❌ <b>Failed to enable bot.</b>\n\n"
            "Please check the logs for more information.",
            parse_mode=ParseMode.HTML
        )

async def admin_disable_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if set_bot_status(False):
        await query.edit_message_text(
            "✅ <b>Bot Disabled Successfully!</b>\n\n"
            "The bot is now offline. Only admins can use it.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            "❌ <b>Failed to disable bot.</b>\n\n"
            "Please check the logs for more information.",
            parse_mode=ParseMode.HTML
        )






async def admin_view_all_members_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    # Handle page from callback data if present
    if query and query.data.startswith("admin_view_unbanned_page_"):
        page = int(query.data.replace("admin_view_unbanned_page_", ""))

    users_data = load_json(USERS_JSON_FILE)
    current_time = time.time()
    all_unbanned = []
    
    for uid, data in users_data.items():
        username = data.get('username')
        # Skip if currently banned
        if username and username in banned_users:
            expiry = banned_users[username]
            if isinstance(expiry, (int, float)) and expiry > current_time:
                continue
        
        # Get display name
        display_name = f"@{username}" if username and not is_fallback_username(username) else "User"
        full_name = data.get('full_name')
        if full_name:
            display_name = f"{full_name} ({display_name})"
        elif not username or is_fallback_username(username):
            display_name = username or f"ID: {uid}"

        target_id = username if username else uid
        all_unbanned.append((target_id, display_name))

    total_members = len(all_unbanned)
    per_page = 10
    total_pages = (total_members + per_page - 1) // per_page if total_members > 0 else 1
    
    # Ensure page is within bounds
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_members = all_unbanned[start_idx:end_idx]

    keyboard = []
    for target_id, display_name in page_members:
        keyboard.append([InlineKeyboardButton(f"👤 {display_name}", callback_data=f"admin_manage_member_{target_id}")])

    # Pagination buttons (Tabs)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_view_unbanned_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_view_unbanned_page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])
    
    message_text = f"👥 <b>All Members (Unbanned)</b>\n"
    message_text += f"Total: {total_members} | Page: {page + 1}/{total_pages}\n\n"
    message_text += "Click a member to manage or ban them."

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_manage_member_options(update: Update, context: ContextTypes.DEFAULT_TYPE, target_id: str):
    query = update.callback_query
    
    # target_id could be username or UID
    users_data = load_json(USERS_JSON_FILE)
    user_data = {}
    if target_id in users_data:
        user_data = users_data[target_id]
    else:
        for uid, data in users_data.items():
            if data.get('username') == target_id:
                user_data = data
                break
    
    username = user_data.get('username', target_id)
    full_name = user_data.get('full_name', 'Unknown')
    
    message_text = (f"👤 <b>Manage Member: {full_name}</b>\n"
                    f"🆔 ID/User: <code>{username}</code>\n\n"
                    f"Select an action:")

    keyboard = [
        [InlineKeyboardButton("🚫 Ban (Default 24h)", callback_data=f"admin_reban_default_{username}")],
        [InlineKeyboardButton("⏱️ Ban (Custom Time)", callback_data=f"admin_reban_custom_{username}")],
        [InlineKeyboardButton("💎 Manage Premium", callback_data=f"admin_manage_prem_{user_data.get('user_id', target_id)}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="admin_view_all_members_interactive")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def admin_view_premium_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    users_data = load_json(USERS_JSON_FILE)
    premium_users = []
    now = datetime.datetime.now()

    for uid, data in users_data.items():
        premium_until = data.get('premium_until')
        if premium_until:
            try:
                expiry_dt = datetime.datetime.strptime(premium_until, '%Y-%m-%d %H:%M:%S')
                if expiry_dt > now:
                    username = data.get('username', f"user{uid}")
                    premium_users.append((uid, username, premium_until))
            except ValueError:
                continue

    keyboard = []
    if not premium_users:
        message_text = "💎 <b>No active premium users found.</b>"
    else:
        message_text = f"💎 <b>Active Premium Users ({len(premium_users)}):</b>\n\nClick a user to manage their premium status."
        # Show first 15 premium users
        for uid, username, expiry in premium_users[:15]:
            display_name = format_username_display(username)
            keyboard.append([InlineKeyboardButton(f"👤 {display_name} (Exp: {expiry[:10]})", callback_data=f"admin_manage_prem_{uid}")])

    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def admin_manage_premium_user_options(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid: str):
    query = update.callback_query
    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(target_uid, {})
    username = user_data.get('username', f"user{target_uid}")
    expiry = user_data.get('premium_until', 'N/A')

    message_text = (
        f"💎 <b>Manage Premium: {format_username_display(username)}</b>\n"
        f"🆔 ID: <code>{target_uid}</code>\n"
        f"📅 Current Expiry: {expiry}\n\n"
        f"Select an action:"
    )

    keyboard = [
        [InlineKeyboardButton("⏳ Set New Duration", callback_data=f"admin_grant_premium_{target_uid}")],
        [InlineKeyboardButton("❌ Remove Premium", callback_data=f"admin_remove_prem_confirm_{target_uid}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="admin_view_premium_users")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_view_temp_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    current_time = time.time()
    keyboard = []
    temp_banned_count = 0
    users_data = load_json(USERS_JSON_FILE)
    
    # Map usernames to user_ids for easier lookup
    username_to_id = {data.get('username'): uid for uid, data in users_data.items() if data.get('username')}

    for username, expiry in banned_users.items():
        if isinstance(expiry, (int, float)) and expiry > current_time:
            temp_banned_count += 1
            seconds_left = int(expiry - current_time)
            hours = seconds_left // 3600
            minutes = (seconds_left % 3600) // 60
            
            # Get display name: Username if exists, else Full Name from DB, else fallback
            uid = username_to_id.get(username)
            display_name = f"@{username}" if username and not is_fallback_username(username) else "User"
            if uid and uid in users_data:
                full_name = users_data[uid].get('full_name')
                if full_name:
                    display_name = f"{full_name} ({display_name})"
            elif not username or is_fallback_username(username):
                display_name = username or "Unknown"

            keyboard.append([InlineKeyboardButton(f"🚫 {display_name} ({hours}h {minutes}m)", callback_data=f"admin_manage_temp_ban_{username}")])

    if temp_banned_count == 0:
        message_text = "📭 <b>No users are currently temporarily banned.</b>"
    else:
        message_text = f"🚫 <b>Temporarily Banned Users ({temp_banned_count}):</b>\n\nClick a user to manage their ban."

    keyboard = keyboard[:15] # Limit to 15 for UI
    keyboard.append([InlineKeyboardButton("🔓 Unban All Temp", callback_data="admin_unban_all_temp")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def admin_manage_temp_ban_options(update: Update, context: ContextTypes.DEFAULT_TYPE, target_username: str):
    query = update.callback_query
    expiry = banned_users.get(target_username, 0)
    current_time = time.time()
    seconds_left = int(expiry - current_time) if expiry > current_time else 0
    
    message_text = (f"🚫 <b>Manage Ban: {format_username_display(target_username)}</b>\n"
                    f"⏳ Time Left: {seconds_left // 3600}h {(seconds_left % 3600) // 60}m\n\n"
                    f"Select an action:")

    keyboard = [
        [InlineKeyboardButton("🔓 Unban Now", callback_data=f"admin_unban_user_{target_username}")],
        [InlineKeyboardButton("⏱️ Set Custom Time", callback_data=f"admin_reban_custom_{target_username}")],
        [InlineKeyboardButton("🕒 Set Default (24h)", callback_data=f"admin_reban_default_{target_username}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="admin_view_temp_banned")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)



async def admin_permanent_ban_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to manage bans.",
            parse_mode=ParseMode.HTML
        )
        return

    permanent_ban_count = len(permanent_bans)

    message = (
        f"🚫 <b>Permanent Ban Management</b>\n\n"
        f"Current Permanent Bans: {permanent_ban_count}\n\n"
        f"<b>Options:</b>\n"
        f"• Ban User Permanently: User can never use the bot again\n"
        f"• Unban User: Remove permanent ban\n"
        f"• View Banned Users: List all permanently banned users\n\n"
        f"<i>Permanent bans override all other ban systems.</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🚫 Ban User", callback_data="admin_perm_ban_user"),
            InlineKeyboardButton("✅ Unban User", callback_data="admin_perm_unban_user")
        ],
        [
            InlineKeyboardButton("📋 View Banned", callback_data="admin_view_perm_banned"),
            InlineKeyboardButton("📊 Statistics", callback_data="admin_ban_stats")
        ],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_panel_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_view_perm_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display list of all permanently banned users.
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view banned users.",
            parse_mode=ParseMode.HTML
        )
        return

    if not permanent_bans:
        await query.edit_message_text(
            "🚫 <b>Permanently Banned Users</b>\n\n"
            "<i>No users are currently permanently banned.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back to Ban Menu", callback_data="admin_permanent_ban_menu")
            ]])
        )
        return

    users_data = load_json(USERS_JSON_FILE)

    message = f"🚫 <b>Permanently Banned Users</b>\n\n"
    message += f"<b>Total Banned:</b> {len(permanent_bans)}\n\n"

    for i, user_id in enumerate(sorted(permanent_bans), 1):
        user_data = users_data.get(user_id, {})
        username = user_data.get('username', f'user{user_id}')
        username_display = format_username_display(username)

        message += f"<b>{i}.</b> {username_display}\n"
        message += f"   ID: <code>{user_id}</code>\n"

        # Show additional info if available
        join_date = user_data.get('join_date', 'Unknown')
        if join_date != 'Unknown':
            message += f"   Joined: {join_date[:10]}\n"

        message += "\n"

    keyboard = [
        [
            InlineKeyboardButton("✅ Unban User", callback_data="admin_perm_unban_user"),
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_perm_banned")
        ],
        [
            InlineKeyboardButton("🔓 Unban All", callback_data="admin_unban_all_users"),
            InlineKeyboardButton("⬅️ Back to Ban Menu", callback_data="admin_permanent_ban_menu")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_ban_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display statistics about bans (permanent and temporary).
    """
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to view ban statistics.",
            parse_mode=ParseMode.HTML
        )
        return

    # Count permanent bans
    permanent_ban_count = len(permanent_bans)

    # Count temporary bans (from ban_data)
    ban_data = load_json(BAN_DATA_FILE)
    temp_ban_count = len(ban_data)

    # Get users data for additional info
    users_data = load_json(USERS_JSON_FILE)

    # Calculate ban statistics
    total_users = len(users_data)
    banned_percentage = ((permanent_ban_count + temp_ban_count) / total_users * 100) if total_users > 0 else 0

    message = "📊 <b>Ban Statistics</b>\n\n"

    message += f"<b>Overview:</b>\n"
    message += f"• Total Users: {total_users}\n"
    message += f"• Permanently Banned: {permanent_ban_count}\n"
    message += f"• Temporarily Banned: {temp_ban_count}\n"
    message += f"• Ban Rate: {banned_percentage:.2f}%\n\n"

    # Show recently banned users (permanent)
    if permanent_bans:
        message += f"<b>🚫 Recently Permanently Banned:</b>\n"
        for user_id in list(permanent_bans)[:5]:
            user_data = users_data.get(user_id, {})
            username = user_data.get('username', f'user{user_id}')
            username_display = format_username_display(username)
            message += f"• {username_display} (<code>{user_id}</code>)\n"
        message += "\n"

    # Show temporarily banned users
    if ban_data:
        message += f"<b>⏳ Currently Temp Banned:</b>\n"
        count = 0
        for user_id, ban_info in ban_data.items():
            if count >= 5:
                break
            user_data = users_data.get(user_id, {})
            username = user_data.get('username', f'user{user_id}')
            username_display = format_username_display(username)
            until = ban_info.get('until', 'Unknown')
            message += f"• {username_display} until {until[:16]}\n"
            count += 1

        if len(ban_data) > 5:
            message += f"<i>... and {len(ban_data) - 5} more</i>\n"

    keyboard = [
        [
            InlineKeyboardButton("📍 View Banned", callback_data="admin_view_perm_banned"),
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_ban_stats")
        ],
        [InlineKeyboardButton("⬅️ Back to Ban Menu", callback_data="admin_permanent_ban_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def admin_set_points_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to set points.",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['awaiting_admin_input'] = 'set_points_all_users'
    context.user_data['return_to_menu'] = 'admin_points_menu'

    await query.edit_message_text(
        "💰 <b>Set Points for All Users</b>\n\n"
        "Enter the points value to set for ALL users:\n\n"
        "<i>Example: 100 (will set every user's points to 100)</i>\n\n"
        "Type /cancel_admin to cancel.",
        parse_mode=ParseMode.HTML
    )

async def admin_recalculate_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to recalculate points.",
            parse_mode=ParseMode.HTML
        )
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Recalculate", callback_data="admin_confirm_recalculate"),
            InlineKeyboardButton("❌ Cancel", callback_data="admin_points_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🔄 <b>Recalculate Points Based on Referrals</b>\n\n"
        "This will recalculate all users' points based on their referral count.\n"
        "Formula: Points = Referrals × Points per Referral\n\n"
        "⚠️ <b>Warning:</b> This will overwrite current points!\n\n"
        "Are you sure you want to continue?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

def calculate_user_points_breakdown(user_id: str) -> dict:
    """
    Calculate user's points breakdown considering:
    - Referrals (reffla*reelar_point)
    - Default join points
    - Points spent on services (sum(claims_services * them_price))
    - Points from redeemed point keys (sum(redeem_points))
    - Net points from transfers (sum(received) - sum(sent))
    """
    try:
        # Get configuration values
        points_per_referral = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
        default_points_on_join = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])

        # Get user data
        users_data = load_json(USERS_JSON_FILE)
        user_data = users_data.get(str(user_id), {})
        username = user_data.get('username', f"user{user_id}")
        referral_count = int(user_data.get('total_referrals', 0))

        # 1. Base points from referrals and default join
        points_from_referrals = referral_count * points_per_referral
        base_points = points_from_referrals + default_points_on_join

        # 2. Points spent on services (claims)
        service_prices_data = load_json(SERVICE_PRICES_FILE)
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
        user_claims = claims_data.get(str(user_id), [])

        total_spent = 0
        purchases = []

        # Group claims by service
        service_counts = {}
        for claim in user_claims:
            service_key = claim.get('service_key')
            if service_key:
                service_counts[service_key] = service_counts.get(service_key, 0) + 1

        # Calculate total spent on services
        for service_key, count in service_counts.items():
            service_price = 100  # Default price
            if service_key in service_prices_data:
                price_data = service_prices_data[service_key]
                if isinstance(price_data, dict):
                    service_price = price_data.get('points_cost', 100)
                else:
                    service_price = price_data  # Old format

            spent_on_service = service_price * count
            total_spent += spent_on_service
            purchases.append({
                'service_key': service_key,
                'count': count,
                'price_per_item': service_price,
                'total_spent': spent_on_service
            })

        # 3. Points from redeemed point keys
        total_redeemed = 0
        point_keys_data = load_json(POINT_KEYS_FILE)
        for key, key_info in point_keys_data.items():
            if key_info.get('used_by') == str(user_id) and key_info.get('status') == 'used':
                total_redeemed += int(key_info.get('points', 0))

        # 4. Net points from transfers (received - sent)
        net_transfers = 0
        transfers_data = load_json(TRANSFERS_FILE)
        for transfer_id, transfer in transfers_data.items():
            if transfer.get('recipient_id') == str(user_id):
                net_transfers += int(transfer.get('amount', 0))
            elif transfer.get('sender_id') == str(user_id):
                net_transfers -= int(transfer.get('amount', 0))

        # 5. Calculate expected points
        expected_points = base_points - total_spent + total_redeemed + net_transfers

        # Ensure points don't go negative
        if expected_points < 0:
            expected_points = 0

        return {
            'user_id': user_id,
            'username': username,
            'expected_points': expected_points,
            'breakdown': {
                'base_points': base_points,
                'points_from_referrals': points_from_referrals,
                'default_points_on_join': default_points_on_join,
                'referral_count': referral_count,
                'points_per_referral': points_per_referral,
                'total_spent': total_spent,
                'total_redeemed': total_redeemed,
                'net_transfers': net_transfers,
                'purchases': purchases,
                'formula': f"({referral_count} × {points_per_referral}) + {default_points_on_join} - {total_spent} + {total_redeemed} + {net_transfers} = {expected_points}"
            }
        }

    except Exception as e:
        logger.error(f"Error calculating points breakdown for user {user_id}: {e}")
        return {'error': str(e)}


def recalculate_points_based_on_referrals_with_details():
    """Recalculate all users' points with comprehensive breakdown"""
    try:
        users_data = load_json(USERS_JSON_FILE)
        points_per_referral = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
        default_points_on_join = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])

        updated_count = 0
        detailed_report = []

        for user_id, user_data in users_data.items():
            username = user_data.get('username', f"user{user_id}")

            # Calculate the comprehensive breakdown
            breakdown = calculate_user_points_breakdown(user_id)

            if 'error' in breakdown:
                logger.error(f"Skipping user {user_id}: {breakdown['error']}")
                continue

            expected_points = breakdown['expected_points']
            current_points = get_user_points(user_id)

            if expected_points != current_points:
                # Calculate the difference
                point_difference = expected_points - current_points

                # Update user points with the difference
                update_user_points(user_id, username, point_difference)

                updated_count += 1

                # Create detailed log entry
                log_entry = (
                    f"User {user_id} ({username}):\n"
                    f"  Current: {current_points} -> New: {expected_points}\n"
                    f"  Formula: {breakdown['breakdown']['formula']}\n"
                    f"  Breakdown:\n"
                    f"    • Referrals: {breakdown['breakdown']['referral_count']} × {breakdown['breakdown']['points_per_referral']} = +{breakdown['breakdown']['points_from_referrals']}\n"
                    f"    • Default on join: +{breakdown['breakdown']['default_points_on_join']}\n"
                    f"    • Total spent on services: -{breakdown['breakdown']['total_spent']}\n"
                    f"    • Points from key redemptions: +{breakdown['breakdown']['total_redeemed']}\n"
                    f"    • Net transfers: {breakdown['breakdown']['net_transfers']:+d}\n"
                )

                if breakdown['breakdown']['purchases']:
                    log_entry += "  Purchases:\n"
                    for purchase in breakdown['breakdown']['purchases']:
                        service_name = purchase['service_key']
                        if purchase['service_key'] in services:
                            service_info = services[purchase['service_key']]
                            if isinstance(service_info, dict):
                                service_name = service_info.get('text', purchase['service_key'])
                            else:
                                service_name = service_info
                        log_entry += f"    • {service_name}: {purchase['count']}×{purchase['price_per_item']} = {purchase['total_spent']} pts\n"

                detailed_report.append(log_entry)

                logger.info(f"Recalculated points for user {user_id}: {current_points} -> {expected_points}")

        # Save detailed report to file
        if detailed_report:
            report_file = DATA_DIR / f"points_recalculation_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("POINTS RECALCULATION REPORT\n")
                f.write(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total users processed: {len(users_data)}\n")
                f.write(f"Users updated: {updated_count}\n")
                f.write(f"Points per referral: {points_per_referral}\n")
                f.write(f"Default points on join: {default_points_on_join}\n\n")
                f.write("=" * 50 + "\n\n")

                for entry in detailed_report:
                    f.write(entry + "\n" + "=" * 50 + "\n\n")

            logger.info(f"Detailed report saved to: {report_file}")

        logger.info(f"Recalculated points for {updated_count} users based on comprehensive calculation")
        return updated_count, len(users_data)

    except Exception as e:
        logger.error(f"Error recalculating points: {e}", exc_info=True)
        return 0, 0

async def admin_confirm_recalculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Send initial message
    progress_message = await query.message.edit_text(
        "🔄 <b>Recalculating Points...</b>\n\n"
        "This may take a moment. Please wait...",
        parse_mode=ParseMode.HTML
    )

    try:
        # Perform recalculation
        updated_count, total_users = recalculate_points_based_on_referrals_with_details()

        # Get configuration values
        points_per_referral = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
        default_points_on_join = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])

        success_message = (
            f"✅ <b>Points Recalculated Successfully!</b>\n\n"
            f"📊 <b>Statistics:</b>\n"
            f"• Total users processed: {total_users}\n"
            f"• Users updated: {updated_count}\n\n"
            f"📝 <b>Formula Used:</b>\n"
            f"Points = (Referrals × {points_per_referral}) + {default_points_on_join} - Total Spent on Purchases\n\n"
            f"📋 <b>Detailed Report:</b>\n"
            f"A detailed report has been saved to the data directory.\n\n"
            f"<i>Users' points have been adjusted based on their referral counts and service purchases.</i>"
        )

        await progress_message.edit_text(
            success_message,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Error in points recalculation: {e}", exc_info=True)
        await progress_message.edit_text(
            f"❌ <b>Error Recalculating Points</b>\n\n"
            f"An error occurred during recalculation:\n"
            f"<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

async def admin_search_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to search users.",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['awaiting_admin_input'] = 'search_user'
    context.user_data['return_to_menu'] = 'admin_panel_main'

    await query.edit_message_text(
        "🔍 <b>Search User</b>\n\n"
        "Enter the user's ID or username to search:\n\n"
        "<i>Examples:</i>\n"
        "<code>123456789</code> (User ID)\n"
        "<code>@username</code> (Username)\n\n"
        "Type /cancel_admin to cancel.",
        parse_mode=ParseMode.HTML
    )

async def admin_export_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.answer("❌ Access denied.", show_alert=True)
        return

    try:
        users_data = load_json(USERS_JSON_FILE)
        points_data = read_csv(USER_POINTS_FILE)

        output = io.StringIO()
        fieldnames = ['user_id', 'username', 'points', 'premium_until', 'referral_count', 'total_claims', 'join_date']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for user_id, user_info in users_data.items():
            points = 0
            for row in points_data:
                if row['user_id'] == user_id:
                    points = row['points']
                    break

            claims_data = load_json(CLAIMED_ACCOUNTS_FILE)
            total_claims = len(claims_data.get(user_id, []))

            writer.writerow({
                'user_id': user_id,
                'username': user_info.get('username', ''),
                'points': points,
                'premium_until': user_info.get('premium_until', ''),
                'referral_count': user_info.get('total_referrals', 0),
                'total_claims': total_claims,
                'join_date': user_info.get('join_date', '')
            })

        output.seek(0)
        csv_data = output.getvalue().encode('utf-8')
        csv_file = io.BytesIO(csv_data)
        csv_file.name = f"members_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        await safe_send_document(
            chat_id=user.id,
            document=csv_file,
            context=context,
            filename=csv_file.name,
            caption="📊 Members Export"
        )

        await query.answer("✅ Members exported and sent to you.", show_alert=True)

    except Exception as e:
        logger.error(f"Error exporting members: {e}")
        await query.answer("❌ Error exporting members.", show_alert=True)

async def admin_export_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.answer("❌ Access denied.", show_alert=True)
        return

    try:
        csv_file = export_referral_stats_to_csv()
        if csv_file:
            csv_data = csv_file.getvalue().encode('utf-8')
            csv_file_obj = io.BytesIO(csv_data)
            csv_file_obj.name = f"referral_stats_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            await safe_send_document(
                chat_id=user.id,
                document=csv_file_obj,
                context=context,
                filename=csv_file_obj.name,
                caption="📈 Referral Statistics Export"
            )

            await query.answer("✅ Referral stats exported and sent to you.", show_alert=True)
        else:
            await query.answer("❌ Error exporting referral stats.", show_alert=True)

    except Exception as e:
        logger.error(f"Error exporting referral stats: {e}")
        await query.answer("❌ Error exporting referral stats.", show_alert=True)

# ------------------ COMMAND HANDLERS ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        logger.warning("No effective_user in update for /start command.")
        return

    if not bot_enabled and not is_admin(user.id):
        await safe_send_message(
            user.id,
            "🔴 <b>Bot is Currently Offline</b>\n\n"
            "The bot is under maintenance. Please check back later.\n\n"
            f"For support, contact @{bot_config.get('primary_admin_contact_username', PRIMARY_ADMIN_USERNAME)}",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    user_id = user.id
    username = user.username if user.username else f"user{user.id}"

    if is_user_permanently_banned(user_id):
        await safe_send_message(
            user.id,
            "🚫 <b>Account Permanently Banned</b>\n\n"
            "Your account has been permanently banned from using this bot.\n\n"
            "This decision is final and cannot be appealed.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    ban_seconds = is_user_banned(username)
    if ban_seconds:
        # If user is permanently banned, notify accordingly
        if ban_seconds == 9999999999:
            await safe_send_message(user.id, "🚫 Your account has been permanently banned.", context, parse_mode=ParseMode.HTML)
        else:
            # For temporary rate limits, inform user about restriction instead of ban
            await safe_send_message(
                user.id,
                f"⛔ You are currently restricted for {ban_seconds} seconds. Please wait.",
                context,
                parse_mode=ParseMode.HTML
            )
        return

    user_requests[username].append(time.time())
    if check_and_ban_user(username, user_id_for_premium_check=user_id):
        ban_duration_msg = is_user_banned(username)
        is_premium_user, _ = check_user_premium_status(user_id)
        admin_contact = bot_config.get("primary_admin_contact_username", PRIMARY_ADMIN_USERNAME)
        # Inform user that they are restricted rather than banned
        msg_restrict = f"You've been restricted for {ban_duration_msg}s due to excessive requests."
        # Provide upgrade instructions to both admin and bot contact
        if not is_premium_user:
            msg_restrict += f"\nConsider upgrading by contacting @{admin_contact} or @akinzoakBot to purchase Premium"
        await safe_send_message(user.id, msg_restrict, context, parse_mode=ParseMode.HTML)
        return

    if not admin_user_ids and username == PRIMARY_ADMIN_USERNAME:
        admin_user_ids.add(user_id)
        save_admins()
        logger.info(f"Primary admin {PRIMARY_ADMIN_USERNAME} (ID: {user_id}) added to admin list.")
        await safe_send_message(user.id, f"You have been recognized as the primary admin.", context, parse_mode=ParseMode.HTML)

    ensure_user_exists(user_id, username)

    if context.args and len(context.args) > 0:
        arg = context.args[0]

        if arg.startswith('ref_'):
            referral_code = arg[4:]
            referrer_id = find_user_by_referral_code(referral_code)

            if referrer_id and str(referrer_id) != str(user_id):
                logger.info(f"Processing referral: Referrer {referrer_id}, Referee {user_id}")
                updated, premium_awarded, new_count = process_referral(referrer_id, user_id)
                if updated:
                    await safe_send_message(
                        user_id,
                        f"🎁 Referral successful! You've received bonus points!",
                        context,
                        parse_mode=ParseMode.HTML
                    )
                    try:
                        referrer_points = get_user_points(referrer_id)
                        await safe_send_message(
                            int(referrer_id),
                            # Format username display for referral notification
                            f"🎉 New referral! User {format_username_display(username)} joined using your link.\n"
                            f"Total referrals: {new_count}\n"
                            f"Your points: {referrer_points}",
                            context,
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e_notify:
                        logger.error(f"Error notifying referrer {referrer_id}: {e_notify}")
                else:
                    await safe_send_message(
                        user_id,
                        "This referral has already been processed.",
                        context,
                        parse_mode=ParseMode.HTML
                    )
            elif str(referrer_id) == str(user_id):
                await safe_send_message(
                    user_id,
                    "You cannot use your own referral link!",
                    context,
                    parse_mode=ParseMode.HTML
                )
            else:
                await safe_send_message(
                    user_id,
                    "Invalid referral link.",
                    context,
                    parse_mode=ParseMode.HTML
                )

    welcome_message = f"Welcome, {user.mention_html()}!\n"

    try:
        users_data = load_json(USERS_JSON_FILE)
        user_count = len(users_data)
        if user_count > 0:
            welcome_message += f"Currently serving {user_count} users.\n"
    except Exception as e_count:
        logger.error(f"Error counting users: {e_count}")

    await safe_send_message(user_id, welcome_message + "Checking your membership status...", context, parse_mode=ParseMode.HTML)
    await check_membership(update, context)

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    if not bot_enabled and not is_admin(user.id):
        await safe_send_message(
            user.id,
            "🔴 <b>Bot is Currently Offline</b>\n\n"
            "The bot is under maintenance. Please check back later.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    if is_user_permanently_banned(user.id):
        await safe_send_message(
            user.id,
            "🚫 <b>Account Permanently Banned</b>\n\n"
            "Your account has been permanently banned from using this bot.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    is_premium, _ = check_user_premium_status(user.id)
    if is_premium or is_admin(user.id):
        await safe_send_message(user.id, f"💎 Premium/Admin status confirmed! Accessing services...", context, parse_mode=ParseMode.HTML)
        await send_main_menu(update, context)
        return

    channels = bot_config.get("channels", [])
    if not channels:
        await safe_send_message(user.id, f"✅ Access granted. No channels required.", context, parse_mode=ParseMode.HTML)
        await send_main_menu(update, context)
        return

    all_joined = True
    failed_channels = []

    for channel in channels:
        ch_id_str = channel.get("chat_id")
        ch_username = channel.get("username", "").lstrip('@')

        if not ch_id_str:
            continue

        try:
            ch_id = int(ch_id_str)
            member_status = await context.bot.get_chat_member(ch_id, user.id)
            if member_status.status not in ["member", "administrator", "creator"]:
                all_joined = False
                failed_channels.append(ch_username or f"ID: {ch_id}")
        except Exception as e:
            logger.error(f"Error checking membership for channel {ch_id_str}: {e}")
            all_joined = False
            failed_channels.append(ch_username or f"ID: {ch_id}")

    if all_joined:
        bonus_given = False
        try:
            users_data = load_json(USERS_JSON_FILE)
            user_data = users_data.get(str(user.id), {})
            bonus_given = user_data.get('bonus_given', False)
        except Exception as e:
            logger.error(f"Error checking bonus_given: {e}")

        if not bonus_given:
            default_points = bot_config.get("default_points_on_join", 1)
            update_user_points(user.id, user.username, default_points)
            users_data = load_json(USERS_JSON_FILE)
            if str(user.id) in users_data:
                users_data[str(user.id)]['bonus_given'] = True
                save_json(USERS_JSON_FILE, users_data)
            await safe_send_message(user.id, f"🎉 You've joined all channels! +{default_points} points bonus!", context, parse_mode=ParseMode.HTML)

        referral_count = get_referral_count(user.id)
        await safe_send_message(user.id, f"✅ Membership confirmed! Accessing services...", context, parse_mode=ParseMode.HTML)
        await send_main_menu(update, context)
    else:
        await prompt_to_join_channels(update, context, failed_channels)

async def prompt_to_join_channels(update: Update, context: ContextTypes.DEFAULT_TYPE, failed_channels=None) -> None:
    channels = bot_config.get("channels", [])
    keyboard = []

    if not channels:
        await safe_send_message(
            update.effective_chat.id,
            "No channels configured. You can access the bot directly.",
            context,
            parse_mode=ParseMode.HTML
        )
        await send_main_menu(update, context)
        return

    for channel in channels:
        ch_username = channel.get("username", "").lstrip('@')
        ch_id = channel.get("chat_id")

        if ch_username:
            url = f"https://t.me/{ch_username}"
            button_text = f"➡️ Join {ch_username}"
        else:
            url = channel.get('invite_link', f"https://t.me/c/{str(ch_id)[4:]}" if str(ch_id).startswith('-100') else f"t.me/{ch_id}")
            button_text = f"➡️ Join Channel"

        keyboard.append([InlineKeyboardButton(button_text, url=url)])

    keyboard.append([InlineKeyboardButton("🔄 I've Joined - Check Again", callback_data="check_membership_again")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if failed_channels:
        channels_list = "\n".join([f"• {ch}" for ch in failed_channels])
        message_text = f"❗ You need to join the following channels to use the bot:\n{channels_list}"
    else:
        message_text = "❗ You need to join our channels to use the bot."

    target_message = update.message or (update.callback_query.message if update.callback_query else None)
    chat_id_to_send = target_message.chat_id if target_message else update.effective_chat.id

    if chat_id_to_send:
        await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def send_referral_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_count: int) -> None:
    user = update.effective_user
    await safe_send_message(update.effective_chat.id, "✅ You've completed the referral challenge! Accessing services...", context, parse_mode=ParseMode.HTML)
    await send_main_menu(update, context)

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    username = user.username if user.username else f"user{user.id}"
    ban_seconds = is_user_banned(username)

    if ban_seconds:
        # User is temporarily restricted due to rate limiting
        await safe_send_message(user.id, f"⛔ Restricted for {ban_seconds}s. Cannot generate referral link.", context, parse_mode=ParseMode.HTML)
        return

    referral_code = get_user_referral_code(user.id)
    referral_count = get_referral_count(user.id)

    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"

    msg = (
        f"🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{referral_link}</code>\n\n"
        f"Share this link with your friends! You both get bonus points.\n\n"
        f"👥 <b>Your Referrals:</b> {referral_count}\n"
    )

    keyboard = [
        [InlineKeyboardButton("↪️ Share Link", switch_inline_query=referral_link)],
        [InlineKeyboardButton("📋 Copy Link", switch_inline_query_current_chat=f"Check out this bot and use my referral link: {referral_link}")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id_to_send = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id
        try:
            await update.callback_query.message.edit_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except BadRequest:
            await safe_send_message(chat_id_to_send, msg, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await safe_send_message(chat_id_to_send, msg, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def redeem_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    username = user.username if user.username else f"user{user.id}"
    ban_seconds = is_user_banned(username)

    if ban_seconds:
        await safe_send_message(user.id, f"⛔ Restricted. Wait {ban_seconds}s before redeeming a key.", context, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    await safe_send_message(user.id, f"🔑 Please enter your redemption key:", context, parse_mode=ParseMode.HTML)
    return WAITING_FOR_KEY

async def process_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        await safe_send_message(update.effective_chat.id, "Invalid input. Key redemption cancelled.", context, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    key = update.message.text.strip()
    username = user.username if user.username else f"user{user.id}"

    valid, key_type = validate_key(key, user.id, username)
    if valid and key_type:
        key_validity_config = bot_config.get("key_validity_days", DEFAULT_BOT_CONFIG["key_validity_days"])
        days_val = key_validity_config.get(key_type, 30)
        days_text = f"{days_val} days" if key_type != "lifetime" else "Lifetime"
        days_text_display = f"{days_text} of premium access"

        if update_user_premium_status(user.id, username, key_type):
            await safe_send_message(user.id, f"✅ Key validated! You now have {days_text_display}. Enjoy!", context, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(user.id, f"❌ Error updating premium status.", context, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, f"❌ Invalid or already used key. Check and try again, or contact support.", context, parse_mode=ParseMode.HTML)

    return ConversationHandler.END

async def redeem_point_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    username = user.username if user.username else f"user{user.id}"
    ban_seconds = is_user_banned(username)

    if ban_seconds:
        await safe_send_message(user.id, f"⛔ Restricted. Wait {ban_seconds}s before redeeming a point key.", context, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    await safe_send_message(user.id, f"🔑 Please enter your point redemption key:", context, parse_mode=ParseMode.HTML)
    return WAITING_FOR_KEY

async def process_point_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        await safe_send_message(update.effective_chat.id, "Invalid input. Point key redemption cancelled.", context, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    key = update.message.text.strip()
    username = user.username if user.username else f"user{user.id}"

    valid, points = validate_point_key(key, user.id, username)
    if valid and points is not None:
        update_user_points(user.id, username, points)
        await safe_send_message(user.id, f"✅ Point key validated! Added {points} points to your balance.", context, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, f"❌ Invalid or already used point key. Check and try again, or contact support.", context, parse_mode=ParseMode.HTML)

    return ConversationHandler.END

async def cancel_key_redemption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_send_message(update.effective_user.id, "Key redemption cancelled.", context, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ------------------ POINTS TRANSFER SYSTEM ------------------
async def verify_transfer_participant(user_id: int, context: ContextTypes.DEFAULT_TYPE, reason: str = "transfer") -> bool:
    """Check if a user is in required channels. If not, ban them and return False."""
    if is_admin(user_id):
        return True

    channels = bot_config.get("channels", [])
    if not channels:
        return True

    all_joined = True
    failed_channels = []

    for channel in channels:
        ch_id_str = channel.get("chat_id")
        ch_username = channel.get("username", "").lstrip('@')
        if not ch_id_str:
            continue
        try:
            ch_id = int(ch_id_str)
            member_status = await context.bot.get_chat_member(ch_id, user_id)
            if member_status.status not in ["member", "administrator", "creator"]:
                all_joined = False
                failed_channels.append(ch_username or f"ID: {ch_id}")
        except Exception as e:
            logger.error(f"Error checking membership for user {user_id} in channel {ch_id_str}: {e}")
            all_joined = False
            failed_channels.append(ch_username or f"ID: {ch_id}")

    if not all_joined:
        # Ban the user
        user_id_str = str(user_id)
        users_data = load_json(USERS_JSON_FILE)
        user_info = users_data.get(user_id_str, {})
        username = user_info.get('username', f"user{user_id_str}")
        
        # Apply permanent ban
        permanent_bans.add(user_id_str)
        save_json(PERMANENT_BANS_FILE, list(permanent_bans))
        
        logger.warning(f"User {username} (ID: {user_id}) banned for not joining channels during {reason}.")
        
        # Notify user
        try:
            await safe_send_message(
                user_id,
                "🚫 <b>You was kiked from bot because of fake transfat</b>\n\n"
                "Your account has been banned. All participants must be members of the required channels to perform this action.",
                context,
                parse_mode=ParseMode.HTML
            )
        except:
            pass

        # Notify Admins for decision
        admin_notif = (
            "🚨 <b>New Automatic Ban</b>\n\n"
            f"<b>User:</b> {username}\n"
            f"<b>ID:</b> <code>{user_id_str}</code>\n"
            f"<b>Reason:</b> Not in required channels during {reason}.\n\n"
            "Please decide if you want to unban this user using the Admin Panel."
        )
        
        # Send to Admins
        for admin_id in admin_user_ids:
            try:
                await safe_send_message(admin_id, admin_notif, context, parse_mode=ParseMode.HTML)
            except:
                pass
        
        # Send to Logs Channel
        log_channel = bot_config.get('log_channel')
        if log_channel and log_channel.get('chat_id'):
            try:
                await safe_send_message(log_channel['chat_id'], admin_notif, context, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send ban notification to log channel: {e}")
            
        return False
    return True

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # Check if sender is in channels
    if not await verify_transfer_participant(user.id, context):
        return ConversationHandler.END

    # Check if user is Prime
    if not is_user_premium(user.id):
        await safe_send_message(
            user.id,
            "💎 <b>Premium Feature</b>\n\n"
            "Point transfers are exclusively available for <b>Premium</b> users.\n\n"
            "Upgrade to Premium and unlock these features to enjoy:\n"
            "• Transfer points to other users\n"
            "• <b>No cooldowns</b> on actions\n"
            "• Higher request limits\n\n"
            " Use /redeem with a premium key to upgrade your status!\n\n"
            "You can upgrade to <b>Premium</b> by contacting <b>@akinzoak</b> or <b>@akinzoakBot</b>",
            context,
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    context.user_data['transfer_state'] = 'awaiting_recipient'

    await safe_send_message(
        user.id,
        "💰 <b>Transfer Points</b>\n\n"
        "Enter the username or ID of the user you want to transfer points to.\n"
        "Example: @username or 123456789\n\n"
        "Type /cancel to cancel.",
        context,
        parse_mode=ParseMode.HTML
    )
    return 1

async def get_transfer_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    recipient_input = update.message.text.strip()

    recipient_id = None
    recipient_username = None

    if recipient_input.isdigit():
        recipient_id = recipient_input
        users_data = load_json(USERS_JSON_FILE)
        if recipient_id in users_data:
            recipient_username = users_data[recipient_id].get('username', f"user{recipient_id}")
    else:
        recipient_username = recipient_input.lstrip('@')
        users_data = load_json(USERS_JSON_FILE)
        for uid, data in users_data.items():
            if data.get('username') == recipient_username:
                recipient_id = uid
                break

    if not recipient_id:
        await safe_send_message(
            user.id,
            "❌ User not found. Please check the username or ID and try again.\n"
            "Type /cancel to cancel.",
            context,
            parse_mode=ParseMode.HTML
        )
        return 1

    # Check if recipient is in channels
    if not await verify_transfer_participant(int(recipient_id), context):
        await safe_send_message(
            user.id,
            f"❌ Cannot transfer to {recipient_username}. The recipient is not a member of the required channels and has been banned.",
            context,
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    context.user_data['transfer_recipient_id'] = recipient_id
    context.user_data['transfer_recipient_username'] = recipient_username
    context.user_data['transfer_state'] = 'awaiting_amount'

    await safe_send_message(
        user.id,
        f"👤 <b>Recipient found:</b> {recipient_username}\n\n"
        "How many points do you want to transfer?\n"
        f"Your current balance: {get_user_points(user.id)} points\n\n"
        "Type /cancel to cancel.",
        context,
        parse_mode=ParseMode.HTML
    )
    return 2

async def get_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    amount_text = update.message.text.strip()

    try:
        amount = int(amount_text)
        if amount <= 0:
            raise ValueError("Amount must be positive")

        sender_points = get_user_points(user.id)

        if amount > sender_points:
            await safe_send_message(
                user.id,
                f"❌ Insufficient points. You have {sender_points} points, but trying to transfer {amount} points.\n"
                "Please enter a smaller amount or type /cancel.",
                context,
                parse_mode=ParseMode.HTML
            )
            return 2

        recipient_id = context.user_data.get('transfer_recipient_id')
        recipient_username = context.user_data.get('transfer_recipient_username')

        # Final membership check for both sender and receiver
        if not await verify_transfer_participant(user.id, context, reason="transfer"):
            return ConversationHandler.END
        if not await verify_transfer_participant(int(recipient_id), context, reason="transfer"):
            await safe_send_message(user.id, f"❌ Transfer failed. The recipient ({recipient_username}) has been banned for not being in required channels.", context, parse_mode=ParseMode.HTML)
            return ConversationHandler.END

        update_user_points(user.id, user.username, -amount)
        update_user_points(recipient_id, recipient_username, amount)

        transfer_id = str(uuid.uuid4())
        transfer_data = {
            'transfer_id': transfer_id,
            'sender_id': str(user.id),
            'sender_username': user.username,
            'recipient_id': recipient_id,
            'recipient_username': recipient_username,
            'amount': amount,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        transfers_file = DATA_DIR / 'transfers.json'
        transfers_data = load_json(transfers_file)
        transfers_data[transfer_id] = transfer_data
        save_json(transfers_file, transfers_data)

        await safe_send_message(
            user.id,
            f"✅ <b>Transfer Successful!</b>\n\n"
            f"💰 <b>Amount:</b> {amount} points\n"
            f"👤 <b>To:</b> {recipient_username}\n"
            f"📊 <b>New Balance:</b> {get_user_points(user.id)} points",
            context,
            parse_mode=ParseMode.HTML
        )

        try:
            # Build clickable sender link for recipient notification
            sender_link = format_user_link(str(user.id), user.username or '')
            await safe_send_message(
                int(recipient_id),
                f"💰 <b>You received a points transfer!</b>\n\n"
                f"👤 <b>From:</b> {html.escape(user.full_name)} {sender_link}\n"
                f"💰 <b>Amount:</b> {amount} points\n"
                f"📊 <b>New Balance:</b> {get_user_points(recipient_id)} points",
                context,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify recipient {recipient_id} of transfer: {e}")

    except ValueError:
        await safe_send_message(
            user.id,
            "❌ Invalid amount. Please enter a positive number.\n"
            "Type /cancel to cancel.",
            context,
            parse_mode=ParseMode.HTML
        )
        return 2

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await safe_send_message(
        update.effective_chat.id,
        "❌ Points transfer cancelled.",
        context,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def transfer_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_transfer(update, context)

# ------------------ MAIN MENU ------------------
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False):
    user = update.effective_user
    if not user:
        return

    if not bot_enabled and not is_admin(user.id):
        await safe_send_message(
            user.id,
            "🔴 <b>Bot is Currently Offline</b>\n\n"
            "The bot is under maintenance. Please check back later.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    if is_user_permanently_banned(user.id):
        await safe_send_message(
            user.id,
            "🚫 <b>Account Permanently Banned</b>\n\n"
            "Your account has been permanently banned from using this bot.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    is_premium, premium_expiry = check_user_premium_status(user.id)
    points = get_user_points(user.id)
    referral_count = get_referral_count(user.id)

    premium_expiry_display = premium_expiry if premium_expiry else "N/A"
    status_line = f"💎 Premium (Expires: {premium_expiry_display})" if is_premium else "Standard User"

    message_text = (f"🏠 <b>Main Menu</b>\n\n"
                    f"👤 <b>User:</b> {user.mention_html()}\n"
                    f"📊 <b>Status:</b> {status_line}\n"
                    f"💰 <b>Points:</b> {points} ✨\n"
                    f"👥 <b>Referrals:</b> {referral_count}\n\n"
                    f"Choose an option:")

    keyboard = [
        [InlineKeyboardButton("🎁 Get Services", callback_data="select_category")],
        [InlineKeyboardButton("💰 My Points", callback_data="my_points"), InlineKeyboardButton("👥 Refer Friends", callback_data="my_referral")],
        [InlineKeyboardButton("🔄 Transfer Points", callback_data="transfer_points"), InlineKeyboardButton("🔑 Redeem Key", callback_data="redeem_key_action")],
        [InlineKeyboardButton("💰 Redeem Points", callback_data="redeem_point_action")],
        [InlineKeyboardButton("📊 Live Stocks", callback_data="user_live_stocks")],
        [InlineKeyboardButton("💬 Send Feedback", callback_data="send_feedback")],
        [InlineKeyboardButton("❓ Help / Support", callback_data="help_support")],
    ]

    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id_to_send = update.effective_chat.id
    target_message_obj = update.message

    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id
        target_message_obj = update.callback_query.message

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await target_message_obj.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"Error sending/editing main menu: {e}")
        if chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def select_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=True):
    categories_set = set(s_info.get('category', 'Other') for s_info in services.values() if s_info)
    sorted_categories = sorted([cat for cat in categories_set if cat != 'Other'])

    if 'Other' in categories_set:
        sorted_categories.append('Other')

    if not sorted_categories:
        message_text = f"No service categories available at the moment."
        kb = [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]
    else:
        message_text = f"🗂️ <b>Service Categories</b>\n\nPlease select a category to view services:"
        kb = [[InlineKeyboardButton(f"📁 {cat}", callback_data=f"category_{cat}")] for cat in sorted_categories]
        kb.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(kb)

    chat_id_to_send = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"Error sending/editing category menu: {e}")
        if chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def send_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, category_filter=None, edit_message=True):
    user = update.effective_user
    if not user:
        return

    user_points = get_user_points(user.id)
    keyboard = []
    current_row = []

    filtered_services = {k: v for k, v in services.items() if v and (not category_filter or v.get('category') == category_filter)}

    if not filtered_services:
        msg_text = f"No services found in the '{category_filter}' category." if category_filter else "No services currently available."
        kb = [[InlineKeyboardButton("⬅️ Back to Categories", callback_data="select_category")]]
        reply_markup_kb = InlineKeyboardMarkup(kb)

        chat_id_to_send = update.effective_chat.id
        if update.callback_query and update.callback_query.message:
            chat_id_to_send = update.callback_query.message.chat_id

        try:
            if edit_message and update.callback_query and update.callback_query.message:
                await update.callback_query.message.edit_text(msg_text, reply_markup=reply_markup_kb, parse_mode=ParseMode.HTML)
            elif chat_id_to_send:
                await safe_send_message(chat_id_to_send, msg_text, context, reply_markup=reply_markup_kb, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            logger.error(f"Error sending/editing no services message: {e}")
            if chat_id_to_send:
                await safe_send_message(chat_id_to_send, msg_text, context, reply_markup=reply_markup_kb, parse_mode=ParseMode.HTML)
        return

    for service_key, service_info in filtered_services.items():
        service_items_data = load_json(SERVICE_ITEMS_FILE)
        count = 0
        if service_key in service_items_data and 'items' in service_items_data[service_key]:
            count = len(service_items_data[service_key]['items'])

        price = get_service_price(service_key)
        button_text = f"{service_info.get('text', service_key)} ({count}) - {price} pts"
        current_row.append(InlineKeyboardButton(button_text, callback_data=f"service_{service_key}"))
        current_row.append(InlineKeyboardButton("💬 Feedback", callback_data=f"feedback_{service_key}"))

        if len(current_row) == 2:
            keyboard.append(current_row)
            current_row = []

    if current_row:
        keyboard.append(current_row)

    keyboard.append([InlineKeyboardButton("⬅️ Back to Categories" if category_filter else "⬅️ Back to Main Menu",
                                         callback_data="select_category" if category_filter else "main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    category_title = f"{category_filter} Services" if category_filter else "All Services"

    message_text = (f"🎁 <b>{category_title}</b>\n\n"
                    f"💰 <b>Your points:</b> {user_points} ✨\n\n"
                    f"Select a service (stock shown in brackets):")

    chat_id_to_send = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"Error sending/editing service selection: {e}")
        if chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def handle_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    if not bot_enabled and not is_admin(user.id):
        await query.message.edit_text(
            "🔴 <b>Bot is Currently Offline</b>\n\n"
            "The bot is under maintenance. Please check back later.",
            parse_mode=ParseMode.HTML
        )
        return

    if is_user_permanently_banned(user.id):
        await query.message.edit_text(
            "🚫 <b>Account Permanently Banned</b>\n\n"
            "Your account has been permanently banned from using this bot.",
            parse_mode=ParseMode.HTML
        )
        return

    username = user.username if user.username else f"user{user.id}"
    user_requests[username].append(time.time())

    if check_and_ban_user(username, user_id_for_premium_check=user.id):
        ban_duration_msg = is_user_banned(username)
        # Inform the user about temporary restriction due to high request volume
        await safe_send_message(
            user.id,
            f"⛔ <b>Cooldown Active!</b>\n\n"
            f"You are restricted for {ban_duration_msg}s due to high-frequency requests.\n\n"
            f"💎 <b>Want to skip cooldowns?</b>\n"
            f"Upgrade to <b>Prime Status</b> to enjoy unlimited actions without waiting!\n"
            f"Use /redeem with a premium key to upgrade now.",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    # Membership check before allowing purchase
    if not await verify_transfer_participant(user.id, context, reason="service purchase"):
        return

    service_key = query.data.split('_', 1)[1]
    user_points = get_user_points(user.id)
    service_price = get_service_price(service_key)
    service_info = services.get(service_key)

    if not service_info:
        await safe_send_message(query.message.chat_id, "❌ Service not found or configuration error.", context, parse_mode=ParseMode.HTML)
        return

    if user_points < service_price:
        await safe_send_message(query.message.chat_id,
            f"❌ <b>Insufficient points for {service_info['text']}!</b>\n\n"
            f"<b>Required:</b> {service_price} points\n"
            f"<b>You have:</b> {user_points} points\n\n"
            f"Earn more by referring friends (/referral) or check /help.",
            context, parse_mode=ParseMode.HTML)
        return

    # Enforce claim cooldown per service
    is_premium_user, _ = check_user_premium_status(user.id)
    can_claim, wait_secs = can_user_claim_service(str(user.id), service_key)
    
    # Premium users are exempt from claim cooldowns
    if not can_claim and not is_premium_user:
        # Format wait time into hours/minutes/seconds
        hrs = wait_secs // 3600
        mins = (wait_secs % 3600) // 60
        secs = wait_secs % 60
        if hrs > 0:
            wait_str = f"{hrs}h {mins}m"
        elif mins > 0:
            wait_str = f"{mins}m {secs}s"
        else:
            wait_str = f"{secs}s"
        await safe_send_message(
            query.message.chat_id,
            f"⏳ You must wait {wait_str} before claiming another {service_info['text']} account. \n\n You can upgrade to Premium by contacting @akinzoak or @akinzoakBot",
            context,
            parse_mode=ParseMode.HTML
        )
        return

    service_items_data = load_json(SERVICE_ITEMS_FILE)
    remaining_stock_before = 0
    if service_key in service_items_data and 'items' in service_items_data[service_key]:
        remaining_stock_before = len(service_items_data[service_key]['items'])

    account_content = pop_account_from_service(
        service_key,
        context,
        claimed_by_user_id=str(user.id),
        claimed_by_username=user.username
    )

    selected_item_path_or_content = None
    is_item_a_filepath = False

    if account_content:
        is_cookie_category = service_info.get('category', '').lower() == 'cookies'
        if is_cookie_category:
            temp_content_dir = TEMP_DIR / "content_files"
            temp_content_dir.mkdir(parents=True, exist_ok=True)
            safe_service_key = "".join(c if c.isalnum() else "_" for c in service_key)
            temp_file_name = f"temp_item_{safe_service_key}_{int(time.time())}_{uuid.uuid4().hex[:4]}.txt"
            temp_file_path = temp_content_dir / temp_file_name

            try:
                with open(temp_file_path, 'w', encoding='utf-8') as f_temp:
                    f_temp.write(account_content)
                selected_item_path_or_content = str(temp_file_path)
                is_item_a_filepath = True
            except Exception as e_write_temp:
                logger.error(f"Error writing temporary content file {temp_file_path}: {e_write_temp}")
                selected_item_path_or_content = None
        else:
            selected_item_path_or_content = account_content
            is_item_a_filepath = False

    if not selected_item_path_or_content:
        await safe_send_message(query.message.chat_id, f"❌ Sorry, {service_info['text']} is currently out of stock. Please try again later.", context, parse_mode=ParseMode.HTML)
        return

    log_claimed_account(str(user.id), username, service_key, account_content)
    
    # Send claim notification to admins/log channel
    await send_claim_notification(str(user.id), username, service_key, account_content, context)

    users_data = load_json(USERS_JSON_FILE)
    if str(user.id) in users_data:
        users_data[str(user.id)]['total_claims'] = users_data[str(user.id)].get('total_claims', 0) + 1
        save_json(USERS_JSON_FILE, users_data)

    initial_points = user_points
    deduct_points(user.id, service_price)
    bonus_points = bot_config.get("points_per_account_bonus", DEFAULT_BOT_CONFIG["points_per_account_bonus"])
    update_user_points(user.id, username, bonus_points)
    final_points = get_user_points(user.id)

    base_caption_html = (f"✅ <b>Here's your {html.escape(service_info['text'])}!</b>\n\n"
                         f"💰 <b>Points:</b> {initial_points} - {service_price} (cost) + {bonus_points} (bonus) = {final_points} remaining ✨\n")

    feedback_button = InlineKeyboardButton("💬 Send Feedback", callback_data=f"feedback_{service_key}")
    reply_markup = InlineKeyboardMarkup([[feedback_button], [InlineKeyboardButton("🏠 Back to Home", callback_data="main_menu")]])

    try:
        bg = random.choice(service_info['backgrounds'])
        if Path(bg).is_file():
            await safe_send_photo(query.message.chat_id, open(bg, 'rb'), context, caption=base_caption_html, parse_mode=ParseMode.HTML)

        if is_item_a_filepath:
            file_to_send_physically = Path(selected_item_path_or_content)
            original_filename_for_telegram = file_to_send_physically.name

            if original_filename_for_telegram.startswith("temp_cookie__"):
                parts = original_filename_for_telegram.split('__', 2)
                if len(parts) > 2:
                    original_filename_for_telegram = parts[2]
            elif original_filename_for_telegram.startswith("temp_item_"):
                parts = original_filename_for_telegram.split('_', 3)
                if len(parts) > 3:
                    original_filename_for_telegram = parts[3]

            final_caption = base_caption_html + f"📄 <b>Filename:</b> <code>{html.escape(original_filename_for_telegram)}</code>"

            with open(file_to_send_physically, 'rb') as doc_file:
                await safe_send_document(chat_id=query.message.chat_id, document=doc_file, context=context,
                                         filename=original_filename_for_telegram, caption=final_caption[:1024], parse_mode=ParseMode.HTML, reply_markup=reply_markup)

            try:
                if file_to_send_physically.parent == (TEMP_DIR / "cookies") or \
                   file_to_send_physically.parent == (TEMP_DIR / "content_files"):
                    file_to_send_physically.unlink()
                    logger.info(f"Cleaned up temporary file: {file_to_send_physically}")
            except Exception as e_clean:
                logger.error(f"Error cleaning temp file {file_to_send_physically}: {e_clean}")
        else:
            # SAFE TEXT DELIVERY WITH LENGTH CHECKS
            account_info_escaped_html = html.escape(selected_item_path_or_content)

            # Telegram limit is 4096 chars for messages, but we need room for caption
            MAX_SAFE_LENGTH = 3800

            if len(account_info_escaped_html) > MAX_SAFE_LENGTH:
                # AUTO-CONVERT TO FILE FOR LONG CONTENT
                temp_content_dir = TEMP_DIR / "content_files"
                temp_content_dir.mkdir(parents=True, exist_ok=True)
                safe_service_key = "".join(c if c.isalnum() else "_" for c in service_key)
                temp_file_name = f"long_content_{safe_service_key}_{int(time.time())}_{uuid.uuid4().hex[:6]}.txt"
                temp_file_path = temp_content_dir / temp_file_name

                try:
                    with open(temp_file_path, 'w', encoding='utf-8') as f_temp:
                        f_temp.write(selected_item_path_or_content)

                    # Send as document instead of text
                    final_caption = base_caption_html + f"📄 <b>Content too long for message</b>\n<i>Full account details attached as file</i>"

                    with open(temp_file_path, 'rb') as doc_file:
                        await safe_send_document(
                            chat_id=query.message.chat_id,
                            document=doc_file,
                            context=context,
                            filename=temp_file_name,
                            caption=final_caption[:1024],
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup
                        )

                    # Cleanup temp file
                    try:
                        temp_file_path.unlink(missing_ok=True)
                    except Exception as e_clean:
                        logger.warning(f"Could not clean temp file {temp_file_path}: {e_clean}")

                    return  # Exit early since we already sent the content

                except Exception as e_write_temp:
                    logger.error(f"Error converting long content to file: {e_write_temp}")
                    # Fallback to truncated text
                    account_info_escaped_html = account_info_escaped_html[:MAX_SAFE_LENGTH] + "...\n<i>[Content truncated due to length]</i>"
            else:
                # Normal text delivery with safe truncation
                if len(account_info_escaped_html) > 4000:
                    account_info_escaped_html = account_info_escaped_html[:3997] + "..."

            message_text = base_caption_html + f"<code>{account_info_escaped_html}</code>"
            await safe_send_message(
                query.message.chat_id,
                message_text,
                context,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
    except Exception as e_send_item:
        logger.error(f"Error sending service item for {service_key} to user {user.id}: {e_send_item}", exc_info=True)
        await safe_send_message(query.message.chat_id, "❌ An error occurred while sending your item. Points have been refunded.", context, parse_mode=ParseMode.HTML)
        update_user_points(user.id, username, service_price - bonus_points)

async def search_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    context.user_data['awaiting_search'] = True
    context.user_data['search_type'] = 'user_stats'

    await safe_send_message(
        user.id,
        "🔍 <b>Search User Statistics</b>\n\n"
        "Enter the user's ID or username to view their statistics:\n\n"
        "<i>Examples:</i>\n"
        "<code>123456789</code> (User ID)\n"
        "<code>@username</code> (Username)\n\n"
        "Type /cancel to cancel.",
        context,
        parse_mode=ParseMode.HTML
    )
    return SEARCH_USER

async def handle_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    search_input = update.message.text.strip()

    if not search_input:
        await safe_send_message(
            user.id,
            "❌ Please enter a user ID or username.",
            context,
            parse_mode=ParseMode.HTML
        )
        return SEARCH_USER

    user_id_to_view = None
    username_to_view = None

    if search_input.isdigit():
        user_id_to_view = search_input
        users_data = load_json(USERS_JSON_FILE)
        user_data = users_data.get(user_id_to_view, {})
        username_to_view = user_data.get('username')
        if not username_to_view:
            username_to_view = f"user{user_id_to_view}"
    else:
        username_to_view = search_input.lstrip('@')
        users_data = load_json(USERS_JSON_FILE)
        for uid, data in users_data.items():
            if data.get('username') == username_to_view:
                user_id_to_view = uid
                break

    if not user_id_to_view:
        await safe_send_message(
            user.id,
            f"❌ User '{search_input}' not found.",
            context,
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    points = get_user_points(user_id_to_view)
    is_premium, premium_until = check_user_premium_status(user_id_to_view)
    referral_count = get_referral_count(user_id_to_view)
    claims = get_user_claims(user_id_to_view, limit=10)

    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(user_id_to_view, {})
    total_claims = user_data.get('total_claims', 0)
    join_date = user_data.get('join_date', 'Unknown')

    claims_text = ""
    if claims:
        claims_text = "\n".join([f"  • {claim.get('service_key')} on {claim.get('timestamp', '')[:10]}" for claim in claims[:5]])
        if len(claims) > 5:
            claims_text += f"\n  • ... and {len(claims) - 5} more claims"
    else:
        claims_text = "  No claims yet"

    # Get transfer history
    transfer_history = get_transfer_history_text(user_id_to_view, limit=3)

    # Get redemption history
    redemption_history = get_redemption_history_text(user_id_to_view, limit=3)

    # Format username for display (avoid '@' on fallback usernames)
    username_display = format_username_display(username_to_view)
    message = (
        f"📊 <b>User Statistics</b>\n\n"
        f"👤 <b>Username:</b> {username_display}\n"
        f"🆔 <b>ID:</b> <code>{user_id_to_view}</code>\n"
        f"📅 <b>Joined:</b> {join_date}\n"
        f"💰 <b>Points:</b> {points}\n"
        f"💎 <b>Premium Status:</b> {'✅ Yes' if is_premium else '❌ No'}\n"
        f"📅 <b>Premium Until:</b> {premium_until if premium_until else 'N/A'}\n"
        f"👥 <b>Referrals:</b> {referral_count}\n"
        f"📦 <b>Total Claims:</b> {total_claims}\n\n"
        f"📋 <b>Recent Claims:</b>\n{claims_text}\n\n"
        f"🔄 <b>Transfer History:</b>\n{transfer_history}\n\n"
        f"🔑 <b>Redemption History:</b>\n{redemption_history}"
    )

    if is_admin(user.id):
        keyboard = [
            [InlineKeyboardButton("📊 Adjust Points", callback_data=f"admin_points_adjust_{user_id_to_view}")],
            [InlineKeyboardButton("💎 Grant Premium", callback_data=f"admin_grant_premium_{user_id_to_view}")],
            [InlineKeyboardButton("⬅️ Back to Main", callback_data="main_menu")]
        ]
    else:
        keyboard = [[InlineKeyboardButton("⬅️ Back to Main", callback_data="main_menu")]]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_send_message(
        user.id,
        message,
        context,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

    context.user_data.pop('awaiting_search', None)
    context.user_data.pop('search_type', None)
    return ConversationHandler.END

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting_search', None)
    context.user_data.pop('search_type', None)
    await safe_send_message(
        update.effective_chat.id,
        "❌ Search cancelled.",
        context,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    try:
        await query.answer()
    except BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old, ignoring answer.")
        else:
            raise

    user = update.effective_user
    if not user:
        return

    username = user.username if user.username else f"user{user.id}"
    ban_seconds = is_user_banned(username)

    if ban_seconds:
        try:
            if query.message:
                # Notify users they are temporarily restricted rather than banned
                await query.message.edit_text(
                    f"⛔ You are currently restricted for {ban_seconds} seconds. Please wait.",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass
        return

    data = query.data

    try:
        if data == "main_menu":
            await send_main_menu(update, context, edit_message=True)

        # ADD THIS IN handle_button_click AFTER existing navigation handlers
        elif data.startswith("claims_service_nav_"):
            try:
                page_index = int(data.replace('claims_service_nav_', ''))
                context.user_data['claims_service_page'] = page_index
                # Re-trigger the admin_claims_by_service view
                mock_update = type('obj', (object,), {
                    'callback_query': type('obj', (object,), {
                        'data': 'admin_claims_by_service',
                        'message': update.callback_query.message,
                        'answer': lambda: None,
                        'edit_message_text': update.callback_query.message.edit_text
                    })(),
                    'effective_user': update.effective_user,
                    'effective_chat': update.effective_chat
                })()
                await admin_callback_router(mock_update, context)
            except (ValueError, IndexError) as e:
                logger.error(f"Error in claims service navigation: {e}")
                await query.answer("❌ Navigation error")
        elif data == "select_category":
            await select_category_menu(update, context, edit_message=True)
        elif data.startswith("category_"):
            await send_service_selection(update, context, category_filter=data.split('_', 1)[1], edit_message=True)
        elif data.startswith("service_"):
            await handle_service_selection(update, context)
        elif data == "my_points":
            await show_points_status(update, context, edit_message=True)
        elif data == "my_referral":
            await referral(update, context)
        elif data == "transfer_points":
            await start_transfer(update, context)
        elif data == "redeem_key_action":
            if query.message:
                await query.message.edit_text("To redeem a premium key, please use the /redeem command directly in the chat.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
        elif data == "redeem_point_action":
            if query.message:
                await query.message.edit_text("To redeem a points key, please use the /redeem_point command directly in the chat.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
        elif data == "help_support":
            await help_command(update, context, edit_message=True)
        elif data == "search_stats":
            await search_user_stats(update, context)
        elif data == "check_membership_again":
            await check_membership(update, context)
        elif data == "admin_panel_main":
            await admin_panel_main_menu(update, context, edit_message=True)
        elif data.startswith("members_nav_"):
            page_index = int(data.replace("members_nav_", ""))
            context.user_data['current_members_page'] = page_index
            await admin_view_all_members(update, context)
        elif data.startswith("claims_user_nav_"):
            page_index = int(data.replace("claims_user_nav_", ""))
            context.user_data['claims_user_page'] = page_index
            await admin_view_claims_by_user(update, context)
        elif data == "admin_view_claimed_accounts":
            await admin_view_claimed_accounts(update, context)
        elif data == "admin_claims_by_user":
            context.user_data['claims_user_page'] = 0
            await admin_view_claims_by_user(update, context)
        elif data == "admin_view_all_members":
            await admin_view_all_members(update, context)
        elif data == "admin_view_referral_stats":
            await admin_view_referral_stats(update, context)

        # View users referral & claim stats
        elif data == "admin_view_users_ref_claims":
            # Initialize page index
            context.user_data['users_ref_claims_page'] = 0
            await admin_view_users_ref_claims(update, context)
        elif data.startswith("users_ref_claims_nav_"):
            try:
                page_index = int(data.replace("users_ref_claims_nav_", ""))
            except ValueError:
                page_index = 0
            context.user_data['users_ref_claims_page'] = page_index
            await admin_view_users_ref_claims(update, context)
        # Navigation for service claim history pages
        elif data.startswith("service_claims_nav_"):
            # Example data: service_claims_nav_<service_key>_<page>
            nav_data = data.replace("service_claims_nav_", "", 1)
            try:
                svc_key, page_str = nav_data.rsplit('_', 1)
            except ValueError:
                svc_key, page_str = nav_data, '0'
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            context.user_data['service_claims_service'] = svc_key
            context.user_data['service_claims_page'] = page
            await admin_view_service_claims(update, context)

        # Detailed user view from the Users Ref & Claims list
        elif data.startswith("admin_user_details_"):
            await admin_user_details(update, context)
        elif data == "admin_manage_bot_status":
            await admin_manage_bot_status(update, context)
        elif data == "admin_enable_bot":
            await admin_enable_bot_handler(update, context)
        elif data == "admin_disable_bot":
            await admin_disable_bot_handler(update, context)
        elif data == "admin_view_bot_status":
            # Display the bot's current status
            await admin_view_bot_status(update, context)
        elif data == "admin_permanent_ban_menu":
            await admin_permanent_ban_menu(update, context)
        elif data == "admin_view_perm_banned":
            await admin_view_perm_banned(update, context)
        elif data == "admin_ban_stats":
            await admin_ban_stats(update, context)
        elif data == "admin_live_stocks":
            await admin_live_stocks(update, context)
        elif data == "user_live_stocks":
            await user_live_stocks(update, context)
        elif data == "admin_export_members":
            await admin_export_members(update, context)
        elif data == "admin_export_referral_stats":
            await admin_export_referral_stats(update, context)
        elif data == "admin_search_user":
            await admin_search_user_menu(update, context)
        elif data == "admin_recalculate_points":
            await admin_recalculate_points(update, context)
        elif data == "admin_confirm_recalculate":
            await admin_confirm_recalculate(update, context)
        elif data.startswith("admin_"):
            await admin_callback_router(update, context)
        elif data == "send_feedback":
            await start_feedback(update, context)
        elif data.startswith("feedback_"):
            if data.startswith("feedback_nav_"):
                await handle_feedback_navigation(update, context)
            elif data.startswith("feedback_reply_") or data.startswith("feedback_mark_") or data.startswith("feedback_delete_"):
                await handle_feedback_actions(update, context)
            elif data.startswith("feedback_confirm_delete_") or data == "feedback_cancel_delete":
                await handle_feedback_delete_confirmation(update, context)
        else:
            logger.warning(f"Unhandled callback query data: {data}")
            if query.message:
                await query.message.edit_text("Unknown action. Please try navigating from the main menu /start.",
                                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"BadRequest in handle_button_click for data '{data}': {e}")
            if "Message can't be edited" in str(e) and query.message:
                await safe_send_message(query.message.chat_id, "An error occurred with that action. Please try again from /start.", context, parse_mode=ParseMode.HTML)
            elif update.effective_chat:
                await safe_send_message(update.effective_chat.id, "An error occurred with that action. Please try again from /start.", context, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Generic error in handle_button_click for data {data}: {e}", exc_info=True)
        if update.effective_chat:
            await safe_send_message(update.effective_chat.id, "An unexpected error occurred. Please try /start.", context, parse_mode=ParseMode.HTML)

async def show_points_status(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False):
    user = update.effective_user
    if not user:
        return

    points = get_user_points(user.id)
    referral_count = get_referral_count(user.id)

    users_data = load_json(USERS_JSON_FILE)
    user_data = users_data.get(str(user.id), {})
    total_claims = user_data.get('total_claims', 0)

    message_text = (f"💰 <b>Your Points Balance</b>\n\n"
                    f"✨ <b>Current Points:</b> {points}\n"
                    f"👥 <b>Total Referrals:</b> {referral_count}\n"
                    f"📦 <b>Total Claims:</b> {total_claims}\n\n"
                    f"<b>You can earn more by:</b>\n"
                    f"• Referring friends (/referral)\n"
                    f"• Claiming accounts (+{bot_config.get('points_per_account_bonus', DEFAULT_BOT_CONFIG['points_per_account_bonus'])} points per claim)\n"
                    f"• Receiving bonuses\n"
                    f"• Admin grants\n"
                    f"• Points transfer from other users")

    keyboard = [
        [InlineKeyboardButton("🔄 Transfer Points", callback_data="transfer_points")],

        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id_to_send = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"Error sending/editing points status: {e}")
        if chat_id_to_send:
            await safe_send_message(chat_id_to_send, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False) -> None:
    user = update.effective_user
    if not user:
        return

    is_premium, premium_until = check_user_premium_status(user.id)
    premium_until_display = premium_until if premium_until else "N/A"
    admin_contact_html = bot_config.get("primary_admin_contact_username", PRIMARY_ADMIN_USERNAME)

    help_text = (
        "📚 <b>Bot Help & Support</b>\n\n"
        "<b>🔹 Commands:</b>\n"
        "• /start - Main menu & membership check.\n"
        "• /referral - Get your referral link.\n"
        "• /redeem - Redeem a premium key.\n"
        "• /redeem_point - Redeem a point key.\n"
        "• /transfer - Transfer points to another user.\n"
        "• /help - This help message.\n"
        "• /data - Get bot data (admin only)\n"
        "• /data_full - Get all data on .zip (admin only)\n"
        "• /upload_data - upload data (admin only)\n\n"
        "<b>💰 Points System:</b>\n"
        f"• Points per referral: {bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])}\n"
        f"• Bonus per claim: {bot_config.get('points_per_account_bonus', DEFAULT_BOT_CONFIG['points_per_account_bonus'])}\n"
        "• Transfer points to other users\n\n"
        "<b>🔍 Search Features:</b>\n"
        "• Search user statistics from main menu\n"
        "• View claim history\n"
        "• Check referral counts\n\n"
        "<b>💬 Feedback System:</b>\n"
        "• Use 'Send Feedback' for general feedback\n"
        "• Service-specific feedback available\n"
        "• Admins will review and reply\n\n"
        "<b>💎 Premium Benefits:</b>\n"
        "• Higher request limits.\n"
        "• Shorter ban times.\n\n"
        f"<b>📞 Support:</b> @{admin_contact_html}"
    )

    if is_premium:
        help_text += f"\n\n✨ <b>Your Status:</b> Premium (Expires: {premium_until_display})"

    if is_admin(user.id):
        help_text += "\n\n👥 <b>Admin Access:</b> Use /admin for the admin panel."

    keyboard = [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id_to_send = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, help_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        logger.error(f"Error sending/editing help command: {e}")
        if chat_id_to_send:
            await safe_send_message(chat_id_to_send, help_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)



# ------------------ ADMIN HANDLERS ------------------



def recalculate_points_based_on_referrals():
    """Recalculate all users' points with comprehensive formula"""
    try:
        users_data = load_json(USERS_JSON_FILE)
        updated_count = 0

        for user_id, user_data in users_data.items():
            username = user_data.get('username', f"user{user_id}")

            # Calculate the comprehensive breakdown
            breakdown = calculate_user_points_breakdown(user_id)

            if 'error' in breakdown:
                logger.error(f"Skipping user {user_id}: {breakdown['error']}")
                continue

            expected_points = breakdown['expected_points']
            current_points = get_user_points(user_id)

            if expected_points != current_points:
                # Calculate the difference
                point_difference = expected_points - current_points

                # Update user points with the difference
                update_user_points(user_id, username, point_difference)

                updated_count += 1
                logger.info(f"Recalculated points for user {user_id}: {current_points} -> {expected_points}")

        logger.info(f"Recalculated points for {updated_count} users based on comprehensive calculation")
        return updated_count
    except Exception as e:
        logger.error(f"Error recalculating points: {e}", exc_info=True)
        return 0

def check_user_points_calculation(user_id: str) -> dict:
    """Check a single user's point calculation details"""
    try:
        users_data = load_json(USERS_JSON_FILE)
        user_data = users_data.get(str(user_id), {})

        if not user_data:
            return {"error": "User not found"}

        points_per_referral = bot_config.get('points_per_referral', DEFAULT_BOT_CONFIG['points_per_referral'])
        default_points_on_join = bot_config.get('default_points_on_join', DEFAULT_BOT_CONFIG['default_points_on_join'])

        # Load service prices
        service_prices_data = load_json(SERVICE_PRICES_FILE)

        # Load all claims data
        claims_data = load_json(CLAIMED_ACCOUNTS_FILE)

        username = user_data.get('username', f"user{user_id}")
        referral_count = int(user_data.get('total_referrals', 0))

        # Calculate points from referrals
        points_from_referrals = referral_count * points_per_referral

        # Add default points on join
        base_points = points_from_referrals + default_points_on_join

        # Calculate points spent on services
        user_claims = claims_data.get(str(user_id), [])
        total_spent = 0
        purchases = []

        # Group claims by service to count purchases
        service_counts = {}
        for claim in user_claims:
            service_key = claim.get('service_key')
            if service_key:
                service_counts[service_key] = service_counts.get(service_key, 0) + 1

        # Calculate total spent with details
        for service_key, count in service_counts.items():
            service_price = 100  # Default price
            if service_key in service_prices_data:
                price_data = service_prices_data[service_key]
                if isinstance(price_data, dict):
                    service_price = price_data.get('points_cost', 100)
                else:
                    service_price = price_data  # Old format

            spent_on_service = service_price * count
            total_spent += spent_on_service
            purchases.append({
                'service_key': service_key,
                'count': count,
                'price_per_item': service_price,
                'total_spent': spent_on_service
            })

        # Calculate expected points
        expected_points = base_points - total_spent

        # Ensure points don't go negative (minimum 0)
        if expected_points < 0:
            expected_points = 0

        # Get current points
        current_points = get_user_points(user_id)

        return {
            'user_id': user_id,
            'username': username,
            'current_points': current_points,
            'expected_points': expected_points,
            'difference': expected_points - current_points,
            'calculation': {
                'referral_count': referral_count,
                'points_per_referral': points_per_referral,
                'points_from_referrals': points_from_referrals,
                'default_points_on_join': default_points_on_join,
                'base_points': base_points,
                'total_spent': total_spent,
                'purchases': purchases,
                'formula': f"({referral_count} × {points_per_referral}) + {default_points_on_join} - {total_spent} = {expected_points}"
            }
        }

    except Exception as e:
        logger.error(f"Error checking user points calculation for {user_id}: {e}")
        return {"error": str(e)}


async def admin_check_user_points_calculation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to check point calculations.",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['awaiting_admin_input'] = 'check_user_points_calculation'
    context.user_data['return_to_menu'] = 'admin_points_menu'

    await query.edit_message_text(
        "🧮 <b>Check User Points Calculation</b>\n\n"
        "Enter the user's ID or username to see detailed point calculation:\n\n"
        "<i>Examples:</i>\n"
        "<code>123456789</code> (User ID)\n"
        "<code>@username</code> (Username)\n\n"
        "Type /cancel_admin to cancel.",
        parse_mode=ParseMode.HTML
    )


async def admin_check_user_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await query.edit_message_text(
            "❌ <b>Access Denied</b>\n\nYou don't have permission to check user points.",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['awaiting_admin_input'] = 'check_user_points'
    context.user_data['return_to_menu'] = 'admin_panel_main'

    await query.edit_message_text(
        "🔍 <b>Check User Points Calculation</b>\n\n"
        "Enter the user's ID or username to check their point calculation:\n\n"
        "<i>Examples:</i>\n"
        "<code>123456789</code> (User ID)\n"
        "<code>@username</code> (Username)\n\n"
        "Type /cancel_admin to cancel.",
        parse_mode=ParseMode.HTML
    )


async def send_enhanced_low_stock_alert(service_key: str, remaining_stock: int, context: ContextTypes.DEFAULT_TYPE,
                                        claimed_by_user_id: str, claimed_by_username: str, claim_time: str):
    """
    Enhanced low stock alert with more details and actions
    """
    try:
        service_name = services.get(service_key, {}).get('text', service_key)
        low_stock_threshold = bot_config.get("low_stock_threshold", 5)
        service_price = get_service_price(service_key)

        # Get user stats
        user_points = get_user_points(claimed_by_user_id)
        is_premium, premium_until = check_user_premium_status(claimed_by_user_id)
        user_claims = get_user_claims(claimed_by_user_id, limit=5)

        # Create detailed message
        message = (
            f"⚠️ <b>LOW STOCK ALERT</b>\n\n"
            f"<b>📱 Service:</b> {service_name}\n"
            f"<b>🔑 Key:</b> <code>{service_key}</code>\n"
            f"<b>💰 Price:</b> {service_price} points\n"
            f"<b>📦 Stock Left:</b> <b>{remaining_stock}</b> / {low_stock_threshold}\n\n"
            f"<b>👤 Claimed By:</b>\n"
            f"• Username: {format_username_display(claimed_by_username or '')}\n"
            f"• User ID: <code>{claimed_by_user_id}</code>\n"
            f"• Points: {user_points}\n"
            f"• Premium: {'✅ Yes' if is_premium else '❌ No'}\n\n"
            f"<b>🕒 Claim Time:</b> {claim_time}\n\n"
            f"<i>This service is running low on stock. Please add more items.</i>"
        )

        # Create inline keyboard with quick actions
        keyboard = [
            [
                InlineKeyboardButton("📊 View User Stats",
                                   callback_data=f"admin_user_stats_{claimed_by_user_id}"),
                InlineKeyboardButton("📦 Add Items",
                                   callback_data=f"admin_add_items_service_{service_key}")
            ],
            [
                InlineKeyboardButton("📋 View Service Claims",
                                   callback_data=f"admin_service_claims_{service_key}"),
                InlineKeyboardButton("⚙️ Adjust Price",
                                   callback_data=f"admin_adjust_price_{service_key}")
            ],
            [
                InlineKeyboardButton("🔄 Check All Services",
                                   callback_data="admin_check_stock_all")
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send to all admins
        for admin_id in admin_user_ids:
            try:
                await safe_send_message(
                    admin_id,
                    message,
                    context,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

                # Also send stock summary for all services
                if remaining_stock == 0:  # Critical stock
                    await send_stock_summary(context, admin_id)

            except Exception as e:
                logger.error(f"Failed to send enhanced low stock alert to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error in enhanced low stock alert: {e}")

async def send_stock_summary(context: ContextTypes.DEFAULT_TYPE, admin_id: int = None):
    """
    Send a summary of all services stock levels
    """
    try:
        low_stock_services = []
        critical_stock_services = []
        normal_stock_services = []

        low_stock_threshold = bot_config.get("low_stock_threshold", 5)

        for service_key, service_info in services.items():
            stock = get_service_stock(service_key)
            service_name = service_info.get('text', service_key)

            if stock == 0:
                critical_stock_services.append((service_name, service_key, stock))
            elif stock <= low_stock_threshold:
                low_stock_services.append((service_name, service_key, stock))
            else:
                normal_stock_services.append((service_name, service_key, stock))

        # Create summary message
        message = "📊 <b>STOCK STATUS SUMMARY</b>\n\n"

        if critical_stock_services:
            message += "🔴 <b>CRITICAL (Out of Stock):</b>\n"
            for name, key, stock in critical_stock_services[:5]:
                message += f"• {name} (<code>{key}</code>): {stock}\n"
            message += "\n"

        if low_stock_services:
            message += "🟡 <b>LOW STOCK:</b>\n"
            for name, key, stock in low_stock_services[:5]:
                message += f"• {name} (<code>{key}</code>): {stock}\n"
            message += "\n"

        message += f"<b>Statistics:</b>\n"
        message += f"• Total Services: {len(services)}\n"
        message += f"• Out of Stock: {len(critical_stock_services)}\n"
        message += f"• Low Stock: {len(low_stock_services)}\n"
        message += f"• In Stock: {len(normal_stock_services)}\n\n"

        if len(critical_stock_services) > 5 or len(low_stock_services) > 5:
            message += f"<i>Showing top 5 items in each category. Use /admin to see full list.</i>"

        # Send to specific admin or all admins
        target_admins = [admin_id] if admin_id else admin_user_ids

        for target_admin_id in target_admins:
            try:
                await safe_send_message(
                    target_admin_id,
                    message,
                    context,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to send stock summary to admin {target_admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error sending stock summary: {e}")
async def check_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command for admins to check stock levels"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.", parse_mode=ParseMode.HTML)
        return

    await send_stock_summary(context, user.id)
    await update.message.reply_text("📊 Stock summary sent!", parse_mode=ParseMode.HTML)

async def notify_admin_low_stock(service_key: str, remaining_stock: int, context: ContextTypes.DEFAULT_TYPE,
                                 claimed_by_user_id: str = None, claimed_by_username: str = None, claim_time: str = None):
    try:
        service_name = services.get(service_key, {}).get('text', service_key)
        low_stock_threshold = bot_config.get("low_stock_threshold", 5)

        # Format the alert message with claim information
        message = (
            f"⚠️ <b>LOW STOCK ALERT</b>\n\n"
            f"<b>Service:</b> {service_name}\n"
            f"<b>Key:</b> <code>{service_key}</code>\n"
            f"<b>Remaining Stock:</b> <b>{remaining_stock}</b>\n"
            f"<b>Threshold:</b> {low_stock_threshold}\n"
        )

        # Add claim information if available
        if claimed_by_username and claim_time:
            # Use helper to format username (avoid '@' on fallback)
            message += f"\n<b>Claimed by</b> {format_username_display(claimed_by_username)} on {claim_time}"

            # Add user ID for admin reference
            if claimed_by_user_id:
                message += f"\n<b>User ID:</b> <code>{claimed_by_user_id}</code>"

        logger.warning(f"Low stock alert for {service_key}: {remaining_stock} items left. Claimed by @{claimed_by_username or 'Unknown'}")

        # Send to all admins
        for admin_id in admin_user_ids:
            try:
                sent_msg = await safe_send_message(
                    admin_id,
                    message,
                    context,
                    parse_mode=ParseMode.HTML
                )
                if sent_msg:
                    try:
                        await context.bot.pin_chat_message(chat_id=admin_id, message_id=sent_msg.message_id)
                    except Exception as pe:
                        logger.error(f"Failed to pin low stock alert for admin {admin_id}: {pe}")
            except Exception as e:
                logger.error(f"Failed to send low stock alert to admin {admin_id}: {e}")

        # Send to logs channel
        log_channel = bot_config.get('log_channel')
        if log_channel and log_channel.get('chat_id'):
            try:
                sent_msg = await safe_send_message(
                    log_channel['chat_id'],
                    message,
                    context,
                    parse_mode=ParseMode.HTML
                )
                if sent_msg:
                    try:
                        await context.bot.pin_chat_message(chat_id=log_channel['chat_id'], message_id=sent_msg.message_id)
                    except Exception as pe:
                        logger.error(f"Failed to pin low stock alert in logs channel: {pe}")
            except Exception as e:
                logger.error(f"Failed to send low stock alert to log channel: {e}")

    except Exception as e:
        logger.error(f"Error preparing low stock notification for {service_key}: {e}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_admin(update.effective_user.id):
        await admin_panel_main_menu(update, context, edit_message=False)
    else:
        await safe_send_message(update.effective_chat.id, "You are not authorized to use this command.", context, parse_mode=ParseMode.HTML)

async def cancel_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await safe_send_message(update.effective_chat.id, "This command is for admins.", context, parse_mode=ParseMode.HTML)
        return

    if 'awaiting_admin_input' in context.user_data:
        del context.user_data['awaiting_admin_input']
        context.user_data.pop('config_key_to_set', None)
        context.user_data.pop('config_description', None)
        context.user_data.pop('service_for_accounts', None)
        context.user_data.pop('return_to_menu', None)

    await safe_send_message(update.effective_chat.id, "Admin input cancelled.", context, parse_mode=ParseMode.HTML)
    await admin_panel_main_menu(update, context, edit_message=False)

async def admin_panel_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=True):
    user = update.effective_user
    if not user or not is_admin(user.id):
        msg = "❌ You don't have permission for the admin panel."
        chat_id_to_send = update.effective_chat.id

        if update.callback_query and update.callback_query.message:
            chat_id_to_send = update.callback_query.message.chat_id
            if edit_message:
                try:
                    await update.callback_query.message.edit_text(msg, parse_mode=ParseMode.HTML)
                except Exception:
                    await safe_send_message(chat_id_to_send, msg, context, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(chat_id_to_send, msg, context, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, msg, context, parse_mode=ParseMode.HTML)
        return

    keyboard = [
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), InlineKeyboardButton("💰 Points Mgt", callback_data="admin_points_menu")],
        [InlineKeyboardButton("📦 Services Mgt", callback_data="admin_services_menu"), InlineKeyboardButton("🔑 Keys Mgt", callback_data="admin_keys_menu")],
        [InlineKeyboardButton("👥 Users Mgt", callback_data="admin_users_menu"), InlineKeyboardButton("📢 Communication", callback_data="admin_comm_menu")],
        [InlineKeyboardButton("📋 View Temp Banned", callback_data="admin_view_temp_banned"), InlineKeyboardButton("🔓 Unban All Temp", callback_data="admin_unban_all_temp")],
        [InlineKeyboardButton("💎 View Premium Users", callback_data="admin_view_premium_users")],
        [InlineKeyboardButton("📩 View Feedbacks", callback_data="admin_view_feedback")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_bot_settings_menu")],
        [InlineKeyboardButton("📦 View Claimed Accounts", callback_data="admin_view_claimed_accounts")],
        [InlineKeyboardButton("📊 Live Stocks", callback_data="admin_live_stocks")],
        [InlineKeyboardButton("👥 View All Members", callback_data="admin_view_all_members")],
        [InlineKeyboardButton("✅ View All Members (Unbanned)", callback_data="admin_view_all_members_interactive")],
        [InlineKeyboardButton("📈 Referral Stats", callback_data="admin_view_referral_stats")],
        [InlineKeyboardButton("👥 Users Ref & Claims", callback_data="admin_view_users_ref_claims")],
        [InlineKeyboardButton("🚫 Permanent Bans", callback_data="admin_permanent_ban_menu")],
        [InlineKeyboardButton("🔄 Bot Status", callback_data="admin_manage_bot_status")],
        [InlineKeyboardButton("🔄 Recalc Points", callback_data="admin_recalculate_points")],
        [InlineKeyboardButton("🔍 Search User", callback_data="admin_search_user")],
        [InlineKeyboardButton("🔍 Search Stats", callback_data="search_stats")],
        [InlineKeyboardButton("⬅️ Back to Main Bot Menu", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    message = "👑 <b>Admin Control Panel</b>\n\nSelect an area to manage:"

    chat_id_to_send = update.effective_chat.id
    target_message_obj = update.message

    if update.callback_query and update.callback_query.message:
        chat_id_to_send = update.callback_query.message.chat_id
        target_message_obj = update.callback_query.message

    try:
        if edit_message and update.callback_query and update.callback_query.message:
            await target_message_obj.edit_text(message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"Error sending/editing admin panel main menu: {e}")
        if "Message can't be edited" in str(e) and chat_id_to_send:
            await safe_send_message(chat_id_to_send, message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        elif chat_id_to_send:
            await safe_send_message(chat_id_to_send, message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def admin_list_services_for_item_add(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type_filter=None):
    query = update.callback_query
    user = update.effective_user

    if not user or not is_admin(user.id):
        if query:
            await query.answer("Unauthorized", show_alert=True)
        return

    context.user_data['awaiting_admin_input'] = 'select_service_for_accounts'

    service_list_md = []
    filter_desc = "All"

    if item_type_filter == 'file':
        filter_desc = "File-Based"
        for sk, si in services.items():
            if 'file' in si:
                service_list_md.append(f"`{sk}` - {si.get('text', sk)}")
    elif item_type_filter == 'folder':
        filter_desc = "Folder-Based"
        for sk, si in services.items():
            if 'folder' in si:
                service_list_md.append(f"`{sk}` - {si.get('text', sk)}")
    else:
        for sk, si in services.items():
            service_list_md.append(f"`{sk}` - {si.get('text', sk)}")

    s_keys_display = "\n".join(service_list_md) if service_list_md else f"No {filter_desc.lower()} services found."
    message_text = (f"📦 <b>Add Items to {filter_desc} Service</b>\n\n"
                    f"Available {filter_desc.lower()} services:\n{s_keys_display}\n\n"
                    f"Enter the <code>service_key</code> for the service you want to add items to (e.g., <code>crunchyroll</code>).\n"
                    f"Or /cancel_admin")

    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Services Mgt", callback_data="admin_services_menu")]])
    context.user_data['return_to_menu'] = 'admin_services_add_items_filter_menu'

    if query and query.message:
        try:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            logger.error(f"BadRequest editing message for admin_list_services_for_item_add: {e}")
            await safe_send_message(query.message.chat_id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ==================== NEW ADMIN FUNCTIONS ====================

async def unban_all_permanently_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Async wrapper to unban all permanently banned users.
    """
    query = update.callback_query
    user = update.effective_user
    
    try:
        unbanned_count, total_count = unban_all_permanently_banned_users()
        
        message_text = (
            f"✅ <b>Unban All Operation Completed</b>\n\n"
            f"Total Banned Users: {total_count}\n"
            f"Successfully Unbanned: {unbanned_count}\n\n"
            f"All permanently banned users have been removed from the ban list."
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
        ])
        
        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            
        logger.info(f"Admin {user.id} unbanned {unbanned_count} users")
        
    except Exception as e:
        logger.error(f"Error in unban_all_permanently_banned: {e}", exc_info=True)
        error_message = f"❌ <b>Error</b>\n\nFailed to unban users: {str(e)}"
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
        ])
        if query and query.message:
            await query.message.edit_text(error_message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(user.id, error_message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def unban_temporary_banned_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Remove all users from temporary ban list.
    """
    query = update.callback_query
    user = update.effective_user
    
    try:
        global banned_users
        
        total_banned = len(banned_users)
        banned_users.clear()
        save_banned_users()
        
        message_text = (
            f"✅ <b>Temporary Ban Removal Completed</b>\n\n"
            f"Total Users Removed from Temp Ban: {total_banned}\n\n"
            f"All users have been removed from the temporary ban list."
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
        ])
        
        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            
        logger.info(f"Admin {user.id} removed {total_banned} users from temporary ban list")
        
    except Exception as e:
        logger.error(f"Error in unban_temporary_banned_users: {e}", exc_info=True)
        error_message = f"❌ <b>Error</b>\n\nFailed to remove temporary bans: {str(e)}"
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
        ])
        if query and query.message:
            await query.message.edit_text(error_message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(user.id, error_message, context, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not user or not is_admin(user.id):
        if query:
            await query.answer("You are not authorized for this action.", show_alert=True)
        return

    action = query.data
    message_text = ""
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]])
    current_parse_mode = ParseMode.HTML

    try:
        if action == "admin_stats":
            users_data = load_json(USERS_JSON_FILE)
            total_users = len(users_data)

            keys_data = load_json(KEYS_FILE)
            active_keys_count = sum(1 for k in keys_data.values() if k.get('status') == 'active')

            low_stock_services = []
            for service_key in services.keys():
                stock = get_service_stock(service_key)
                low_stock_threshold = bot_config.get("low_stock_threshold", 5)
                if stock <= low_stock_threshold:
                    low_stock_services.append(f"{services[service_key].get('text', service_key)}: {stock} items")

            users_data = load_json(USERS_JSON_FILE)
            top_refs = []
            for uid, data in users_data.items():
                ref_count = data.get('total_referrals', 0)
                if ref_count > 0:
                    username = data.get('username', uid)
                    top_refs.append((username, ref_count))

            top_refs.sort(key=lambda x: x[1], reverse=True)
            top_refs_display = "\n".join([f"{username}: {count} referrals" for username, count in top_refs[:3]]) if top_refs else "No referrals yet."

            low_stock_display = "\n".join(low_stock_services[:5]) if low_stock_services else "All services have sufficient stock."

            message_text = (f"📊 <b>Bot Statistics</b>\n\n"
                            f"👥 <b>Total Users:</b> {total_users}\n"
                            f"🔑 <b>Active Keys:</b> {active_keys_count}\n"
                            f"🚫 <b>Permanent Bans:</b> {len(permanent_bans)}\n"
                            f"🔄 <b>Bot Status:</b> {'🟢 Online' if bot_enabled else '🔴 Offline'}\n\n"
                            f"⚠️ <b>Low Stock Services:</b>\n{low_stock_display}\n\n"
                            f"🏆 <b>Top Referrers:</b>\n{top_refs_display}")
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]])

        # ADD THIS BLOCK IN admin_callback_router BEFORE THE FINAL "else" CHECK
        elif action == "admin_claims_by_service":
            all_claims = get_all_claims(limit=500)
            service_claims = {}
            for claim in all_claims:
                sk = claim.get('service_key', 'unknown')
                if sk not in service_claims:
                    service_claims[sk] = []
                service_claims[sk].append(claim)

            # CRITICAL FIX: Always set a valid non-empty message_text
            if not service_claims:
                message_text = "📭 <b>No Claims Found</b>\nNo accounts have been claimed yet."
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_view_claimed_accounts")]
                ])
                current_parse_mode = ParseMode.HTML
            else:
                # Sort services by claim count descending
                sorted_services = sorted(
                    service_claims.items(),
                    key=lambda x: len(x[1]),
                    reverse=True
                )

                current_page = context.user_data.get('claims_service_page', 0)
                services_per_page = 5
                total_pages = (len(sorted_services) + services_per_page - 1) // services_per_page

                if current_page >= total_pages:
                    current_page = max(0, total_pages - 1)

                start_idx = current_page * services_per_page
                end_idx = start_idx + services_per_page
                page_services = sorted_services[start_idx:end_idx]

                # CRITICAL FIX: Always start with non-empty base message
                message_text = f"📱 <b>Claims by Service</b> - Page {current_page + 1}/{total_pages}\n\n"
                for i, (service_key, claims) in enumerate(page_services, start=start_idx + 1):
                    service_name = services.get(service_key, {}).get('text', service_key)
                    claim_count = len(claims)
                    recent_claim = max(claims, key=lambda x: x.get('timestamp', ''))
                    recent_user = recent_claim.get('username') or ''
                    recent_user_id = recent_claim.get('user_id')
                    # If we have a user ID, create a clickable link; otherwise, format username display
                    if recent_user_id:
                        recent_user_link = format_user_link(recent_user_id, recent_user)
                    else:
                        recent_user_link = format_username_display(recent_user) if recent_user else 'Unknown'
                    recent_date = recent_claim.get('timestamp', 'Unknown')[:16]
                    message_text += (
                        f"<b>{i}.</b> <b>{service_name}</b> (<code>{service_key}</code>)\n"
                        f"   Total Claims: {claim_count}\n"
                        f"   Recent: {recent_user_link} on {recent_date}\n\n"
                    )

                keyboard = []
                nav_buttons = []
                if current_page > 0:
                    nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"claims_service_nav_{current_page - 1}"))
                nav_buttons.append(InlineKeyboardButton(f"📄 {current_page + 1}/{total_pages}", callback_data="claims_service_count"))
                if current_page < total_pages - 1:
                    nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"claims_service_nav_{current_page + 1}"))
                if nav_buttons:
                    keyboard.append(nav_buttons)

                keyboard.append([
                    InlineKeyboardButton("🔍 View Service Claims", callback_data="admin_view_specific_service_claims"),
                    InlineKeyboardButton("👤 Back to Overview", callback_data="admin_view_claimed_accounts")
                ])
                reply_markup = InlineKeyboardMarkup(keyboard)
                current_parse_mode = ParseMode.HTML

        elif action == "admin_view_specific_service_claims":
            # Present a list of all services that have been claimed and allow the admin to choose one
            # to view the full claim history for that service.
            # Build a list of service keys with their claim counts
            all_claims = get_all_claims(limit=10000)
            service_counts = {}
            for claim in all_claims:
                sk = claim.get('service_key', 'unknown')
                service_counts[sk] = service_counts.get(sk, 0) + 1
            if not service_counts:
                message_text = (
                    "📭 <b>No Claimed Services</b>\n\n"
                    "No accounts have been claimed yet, so there is no claim history to display."
                )
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_claims_by_service")],
                    [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
                ])
                current_parse_mode = ParseMode.HTML
            else:
                # Sort services by count descending
                sorted_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)
                # Build message listing the services with claim counts
                message_lines = ["📱 <b>Select a Service for Claim History</b>\n"]
                for idx, (svc_key, cnt) in enumerate(sorted_services, start=1):
                    svc_name = services.get(svc_key, {}).get('text', svc_key)
                    plural = 's' if cnt != 1 else ''
                    message_lines.append(f"<b>{idx}.</b> {svc_name} (<code>{svc_key}</code>) - {cnt} claim{plural}")
                message_text = "\n".join(message_lines)
                # Build inline buttons for each service
                keyboard = []
                for svc_key, cnt in sorted_services:
                    svc_name = services.get(svc_key, {}).get('text', svc_key)
                    keyboard.append([
                        InlineKeyboardButton(
                            f"{svc_name} ({cnt})",
                            callback_data=f"admin_service_claims_{svc_key}"
                        )
                    ])
                # Add navigation/back buttons
                keyboard.append([
                    InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_claims_by_service"),
                    InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")
                ])
                reply_markup = InlineKeyboardMarkup(keyboard)
                current_parse_mode = ParseMode.HTML

        elif action == "admin_points_menu":
            message_text = f"💰 <b>Points Management</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Set Service Prices", callback_data="admin_points_set_prices")],
                [InlineKeyboardButton("Adjust User Points", callback_data="admin_points_adjust_usef")],
                [InlineKeyboardButton("View User Points", callback_data="admin_points_view_usef")],
                [InlineKeyboardButton("Check User Calculation", callback_data="admin_check_user_points_calculation")],  # New option
                [InlineKeyboardButton("Generate Point Keys", callback_data="admin_points_generate_point_keys")],
                [InlineKeyboardButton("Set Points for All Users", callback_data="admin_set_points_all_users")],
                [InlineKeyboardButton("🔄 Recalc Based on Formula", callback_data="admin_recalculate_points")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]
            ])
        elif action == "admin_set_points_all_users":
            await admin_set_points_all_users(update, context)
            return

        elif action == "admin_points_add_all":
            context.user_data['awaiting_admin_input'] = 'add_points_all_users'
            context.user_data['return_to_menu'] = 'admin_points_menu'
            message_text = f"Enter points to add to all users (e.g., 50).\nOr /cancel_admin"

        elif action == "admin_points_generate_point_keys":
            context.user_data['awaiting_admin_input'] = 'generate_point_keys'
            context.user_data['return_to_menu'] = 'admin_points_menu'
            message_text = f"Enter count points (e.g., 5 500 to generate 5 keys each giving 500 points).\nOr /cancel_admin"

        elif action == "admin_points_set_prices":
            context.user_data['awaiting_admin_input'] = 'set_service_price'
            context.user_data['return_to_menu'] = 'admin_points_menu'
            message_text = f"Enter service_key new_price (e.g., crunchyroll 150).\nOr /cancel_admin"

        elif action == "admin_points_adjust_usef":
            context.user_data['awaiting_admin_input'] = 'adjust_user_points'
            context.user_data['return_to_menu'] = 'admin_points_menu'
            message_text = f"Enter username_or_id points_to_add_or_subtract (e.g., testuser 100 or 12345678 -50).\nOr /cancel_admin"

        elif action == "admin_points_view_usef":
            context.user_data['awaiting_admin_input'] = 'view_user_points'
            context.user_data['return_to_menu'] = 'admin_points_menu'
            message_text = f"Enter username_or_id to view their points.\nOr /cancel_admin"

        elif action == "admin_services_menu":
            message_text = f"📦 <b>Services Management</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Add New Service", callback_data="admin_services_add")],
                [InlineKeyboardButton("Add Items to Service", callback_data="admin_services_add_items_filter_menu")],
                [InlineKeyboardButton("Remove Service", callback_data="admin_services_remove")],
                [InlineKeyboardButton("Remove All Items from Service", callback_data="admin_services_remove_items")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]
            ])

        elif action == "admin_services_remove_items":
            context.user_data['awaiting_admin_input'] = 'remove_service_items'
            context.user_data['return_to_menu'] = 'admin_services_menu'
            s_keys_list = list(services.keys())
            s_keys_display = ", ".join(s_keys_list) if s_keys_list else "None"
            message_text = (f"Enter service key to REMOVE ALL ITEMS from (e.g., crunchyroll).\n"
                            f"Available: {s_keys_display}\nOr /cancel_admin")

        elif action == "admin_services_add_items_filter_menu":
            message_text = f"📂 <b>Add Items: Select Service Type</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 File-Based Services", callback_data="admin_add_items_filter_file")],
                [InlineKeyboardButton("📁 Folder-Based Services", callback_data="admin_add_items_filter_folder")],
                [InlineKeyboardButton("🌐 All Services", callback_data="admin_add_items_filter_all")],
                [InlineKeyboardButton("⬅️ Back to Services Mgt", callback_data="admin_services_menu")]
            ])

        elif action == "admin_add_items_filter_file":
            await admin_list_services_for_item_add(update, context, item_type_filter='file')
            return

        elif action == "admin_add_items_filter_folder":
            await admin_list_services_for_item_add(update, context, item_type_filter='folder')
            return

        elif action == "admin_add_items_filter_all":
            await admin_list_services_for_item_add(update, context)
            return

        elif action == "admin_services_add":
            context.user_data['awaiting_admin_input'] = 'add_service'
            context.user_data['return_to_menu'] = 'admin_services_menu'
            message_text = (f"<b>Send service details in format:</b>\n"
                            f"<code>key|DisplayName|file_or_folder|path_relative_to_data_dirs|Category</code>\n\n"
                            f"<b>Example for file:</b> <code>newserv|New Service|file|newserv.txt|Streaming</code>\n"
                            f"<b>Example for folder:</b> <code>newcookie|New Cookies|folder|NewCookieFolder|Cookies</code>\n\n"
                            f"Alternatively, upload a .txt file where the first line is the config string above, and subsequent lines are accounts/cookie data.\n"
                            f"Or /cancel_admin")

        elif action == "admin_services_remove":
            context.user_data['awaiting_admin_input'] = 'remove_service'
            context.user_data['return_to_menu'] = 'admin_services_menu'
            s_keys_list = list(services.keys())
            s_keys_display = ", ".join(s_keys_list) if s_keys_list else "None"
            message_text = (f"Enter service key to REMOVE (e.g., crunchyroll).\n"
                            f"Available: {s_keys_display}\nOr /cancel_admin")

        elif action == "admin_keys_menu":
            message_text = f"🔑 <b>Keys Management</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Generate Lifetime Keys", callback_data="admin_keys_generate")],
                [InlineKeyboardButton("View Active Keys", callback_data="admin_keys_view")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]
            ])

        elif action == "admin_keys_generate":
            context.user_data['awaiting_admin_input'] = 'generate_keys'
            context.user_data['return_to_menu'] = 'admin_keys_menu'
            message_text = (f"Enter count for lifetime keys (e.g., 5 for 5 lifetime keys).\n"
                            f"Or /cancel_admin")

        elif action == "admin_keys_view":
            keys_data = load_json(KEYS_FILE)
            active_k_list = []
            for key, data in keys_data.items():
                if data.get('status') == 'active':
                    active_k_list.append(f"Key: {key}, Type: {data.get('type', 'unknown')}")

            message_text = f"🔑 <b>Active Keys:</b>\n\n" + ("\n".join(active_k_list) if active_k_list else "No active keys.")
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_keys_menu")]])

        elif action == "admin_users_menu":
            message_text = f"👥 <b>Users Management</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 Ban User (Temp)", callback_data="admin_users_ban")],
                [InlineKeyboardButton("✅ Unban User (Temp)", callback_data="admin_users_unban")],
                [InlineKeyboardButton("📋 View Temp Banned", callback_data="admin_view_temp_banned")],
                [InlineKeyboardButton("🔓 Unban All Temp", callback_data="admin_unban_all_temp")],
                [InlineKeyboardButton("💎 Grant Premium", callback_data="admin_users_grant_premium")],
                [InlineKeyboardButton("❌ Remove Premium", callback_data="admin_users_remove_premium")],
                [InlineKeyboardButton("🚫 Permanent Ban", callback_data="admin_perm_ban_user")],
                [InlineKeyboardButton("✅ Permanent Unban", callback_data="admin_perm_unban_user")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]
            ])

        elif action == "admin_view_temp_banned":
            await admin_view_temp_banned(update, context)
            return

        elif action.startswith("admin_manage_temp_ban_"):
            target_username = action.replace("admin_manage_temp_ban_", "")
            await admin_manage_temp_ban_options(update, context, target_username)
            return

        elif action.startswith("admin_reban_default_"):
            target_username = action.replace("admin_reban_default_", "")
            # Find user_id if possible
            target_uid = None
            for uid, data in load_json(USERS_JSON_FILE).items():
                if data.get('username') == target_username:
                    target_uid = int(uid)
                    break
            check_and_ban_user(target_username, user_id_for_premium_check=target_uid, manual=True)
            await query.answer(f"✅ Ban reset to 24h for {target_username}", show_alert=True)
            await admin_view_temp_banned(update, context)
            return

        elif action.startswith("admin_reban_custom_"):
            target_username = action.replace("admin_reban_custom_", "")
            context.user_data['awaiting_admin_input'] = 'ban_user_manual'
            context.user_data['return_to_menu'] = 'admin_view_temp_banned'
            await query.edit_message_text(f"Enter new ban time in seconds for {target_username}:\nExample: 3600\n\nOr /cancel_admin", parse_mode=ParseMode.HTML)
            return

        elif action.startswith("admin_unban_user_"):
            username_to_unban = action.replace("admin_unban_user_", "")
            success, message = unban_temporary_ban_member(username_to_unban)
            await query.answer(message, show_alert=True)
            await admin_view_temp_banned(update, context)
            return

        elif action == "admin_unban_all_temp":
            await unban_temporary_banned_users(update, context)
            return

        elif action == "admin_users_ban":
            context.user_data['awaiting_admin_input'] = 'ban_user_manual'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter username_or_id [seconds] to ban.\nExample: @username 3600 (default 86399)\nOr /cancel_admin"

        elif action == "admin_users_unban":
            context.user_data['awaiting_admin_input'] = 'unban_user_manual'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter username_or_id to unban.\nOr /cancel_admin"

        elif action == "admin_users_grant_premium":
            context.user_data['awaiting_admin_input'] = 'grant_premium_manual'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter username_or_id days (e.g., username 30 or 123456 3650).\nOr /cancel_admin"

        elif action == "admin_users_remove_premium":
            context.user_data['awaiting_admin_input'] = 'remove_premium_manual'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter username_or_id to remove premium.\nOr /cancel_admin"

        elif action == "admin_perm_ban_user":
            context.user_data['awaiting_admin_input'] = 'permanent_ban_user'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter user_id to ban permanently.\nOr /cancel_admin"

        elif action == "admin_perm_unban_user":
            context.user_data['awaiting_admin_input'] = 'permanent_unban_user'
            context.user_data['return_to_menu'] = 'admin_users_menu'
            message_text = f"Enter user_id to remove permanent ban.\nOr /cancel_admin"

        elif action == "admin_comm_menu":
            message_text = f"📢 <b>Communication</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Post Announcement to Channel", callback_data="admin_comm_post_announce")],
                [InlineKeyboardButton("Broadcast to All Users", callback_data="admin_comm_broadcast_users")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel_main")]
            ])

        elif action == "admin_comm_post_announce":
            context.user_data['awaiting_admin_input'] = 'post_announcement'
            context.user_data['return_to_menu'] = 'admin_comm_menu'
            message_text = (f"Send the message/media for announcement (text, photo, video, document).\n"
                            f"Caption will be used if media is sent.\nOr /cancel_admin")

        elif action == "admin_comm_broadcast_users":
            context.user_data['awaiting_admin_input'] = 'broadcast_to_users'
            context.user_data['return_to_menu'] = 'admin_comm_menu'
            message_text = (f"Send the message/media for broadcast to all users (text, photo, video, document).\n"
                            f"Caption will be used if media is sent.\nOr /cancel_admin")

        elif action == "admin_view_feedback":
            await admin_view_feedbacks(update, context)
            return

        elif action == "admin_bot_settings_menu":
            message_text = f"⚙️ <b>Bot Settings Menu</b>\n\nSelect a setting to view or modify:"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Points Configuration", callback_data="admin_settings_points")],
                [InlineKeyboardButton("Key Validity Durations", callback_data="admin_settings_key_validity")],
                [InlineKeyboardButton("Restriction & Request Limits", callback_data="admin_settings_ban_limits")],
                [InlineKeyboardButton("Manage Admins", callback_data="admin_manage_admins")],
                [InlineKeyboardButton("Manage Channels", callback_data="admin_manage_channels")],
                [InlineKeyboardButton("Low Stock Threshold", callback_data="admin_low_stock_threshold")],
                [InlineKeyboardButton("📋 Log Channel Settings", callback_data="admin_log_channel_settings")],
                [InlineKeyboardButton("🔔 Claim Notifications", callback_data="admin_claim_notifications")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]
            ])

        elif action == "admin_low_stock_threshold":
            context.user_data['awaiting_admin_input'] = 'set_config_value'
            context.user_data['config_key_to_set'] = 'low_stock_threshold'
            context.user_data['config_description'] = 'Low Stock Threshold'
            context.user_data['return_to_menu'] = 'admin_bot_settings_menu'
            current_val = bot_config.get('low_stock_threshold', 5)
            message_text = f"Enter new value for Low Stock Threshold (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_manage_channels":
            channels = bot_config.get("channels", [])
            channels_display = ""
            for ch in channels:
                channels_display += f"• {ch.get('username', 'N/A')} (ID: {ch.get('chat_id', 'N/A')})\n"

            if not channels:
                channels_display = "No channels configured."

            message_text = f"📢 <b>Channels</b>\n\n{channels_display}\nChoose an action:"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")],
                [InlineKeyboardButton("🗑️ Remove Channel", callback_data="admin_channels_remove_menu")],
                [InlineKeyboardButton("⬅️ Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_add_channel":
            context.user_data['awaiting_admin_input'] = 'add_channel'
            context.user_data['return_to_menu'] = 'admin_manage_channels'
            message_text = f"📢 <b>Add Channel</b>\n\nSend channel @username or forward a message from the channel.\n\nOr type the channel info in format: @username chat_id (e.g., @mychannel -1001234567890)\n\nOr /cancel_admin"

        elif action == "admin_channels_remove_menu":
            channels = bot_config.get("channels", [])
            if not channels:
                message_text = "No channels to remove."
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_manage_channels")]])
            else:
                buttons = []
                for ch in channels:
                    ch_name = ch.get('username', 'Unknown')
                    ch_id = ch.get('chat_id')
                    buttons.append([InlineKeyboardButton(f"🗑️ {ch_name}", callback_data=f"admin_remove_channel_{ch_id}")])
                buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_manage_channels")])
                reply_markup = InlineKeyboardMarkup(buttons)
                message_text = "<b>Select a channel to remove:</b>"

        elif action.startswith("admin_remove_channel_"):
            channel_id = action.replace("admin_remove_channel_", "")
            channels = bot_config.get("channels", [])
            updated_channels = [ch for ch in channels if str(ch.get('chat_id')) != channel_id]

            if len(updated_channels) < len(channels):
                bot_config["channels"] = updated_channels
                save_config()
                message_text = f"✅ Channel removed successfully."
            else:
                message_text = f"❌ Channel not found."

            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Channels", callback_data="admin_manage_channels")]])

        elif action == "admin_manage_admins":
            current_admins_str = ", ".join(map(str, admin_user_ids)) if admin_user_ids else "None"
            message_text = (f"⚙️ <b>Manage Admins</b>\n\nCurrent Admin IDs: {current_admins_str}\n\nChoose an action:")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Admin", callback_data="admin_add_admin_prompt")],
                [InlineKeyboardButton("➖ Remove Admin", callback_data="admin_remove_admin_prompt")],
                [InlineKeyboardButton("⬅️ Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_add_admin_prompt":
            context.user_data.update({'awaiting_admin_input': 'add_admin_id', 'return_to_menu': 'admin_manage_admins'})
            message_text = f"Enter the Telegram User ID of the user to add as admin.\nOr /cancel_admin"

        elif action == "admin_remove_admin_prompt":
            context.user_data.update({'awaiting_admin_input': 'remove_admin_id', 'return_to_menu': 'admin_manage_admins'})
            message_text = f"Enter the Telegram User ID of the admin to remove.\nOr /cancel_admin"

        elif action == "admin_log_channel_settings":
            log_channel = bot_config.get('log_channel')
            if log_channel and log_channel.get('chat_id'):
                channel_info = f"\n\n<b>Current Log Channel:</b>\n"
                channel_info += f"\u2022 Username: {log_channel.get('username', 'N/A')}\n"
                channel_info += f"\u2022 Chat ID: <code>{log_channel.get('chat_id')}</code>"
            else:
                channel_info = "\n\n<b>No log channel configured.</b>"
            
            message_text = f"\U0001F4CB <b>Log Channel Settings</b>{channel_info}\n\nThe log channel receives claim notifications when notification mode is set to 'log_channel' or 'both'."
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2795 Set Log Channel", callback_data="admin_set_log_channel")],
                [InlineKeyboardButton("\U0001F5D1\ufe0f Remove Log Channel", callback_data="admin_remove_log_channel")],
                [InlineKeyboardButton("\u2b05\ufe0f Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_set_log_channel":
            context.user_data['awaiting_admin_input'] = 'set_log_channel'
            context.user_data['return_to_menu'] = 'admin_log_channel_settings'
            message_text = ("\U0001F4CB <b>Set Log Channel</b>\n\n"
                           "Send the channel info in one of these formats:\n\n"
                           "1. Forward a message from the channel\n"
                           "2. Type: <code>@username chat_id</code>\n"
                           "   Example: <code>@mylogchannel -1001234567890</code>\n"
                           "3. Just the chat ID: <code>-1001234567890</code>\n\n"
                           "<i>Make sure the bot is an admin in the channel!</i>\n\n"
                           "Or /cancel_admin")

        elif action == "admin_remove_log_channel":
            log_channel = bot_config.get('log_channel')
            if log_channel:
                bot_config['log_channel'] = None
                save_config()
                message_text = "\u2705 <b>Log channel removed successfully!</b>"
            else:
                message_text = "\u26a0\ufe0f <b>No log channel was configured.</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2b05\ufe0f Back to Log Channel Settings", callback_data="admin_log_channel_settings")]
            ])

        elif action == "admin_claim_notifications":
            current_mode = bot_config.get('claim_notification_mode', 'admins')
            mode_descriptions = {
                'admins': '\U0001F464 Admins Only - Notifications sent to all admins',
                'log_channel': '\U0001F4E2 Log Channel Only - Notifications sent to log channel',
                'both': '\U0001F501 Both - Notifications sent to admins AND log channel',
                'none': '\U0001F515 Disabled - No claim notifications'
            }
            current_desc = mode_descriptions.get(current_mode, 'Unknown')
            
            message_text = (f"\U0001F514 <b>Claim Notification Settings</b>\n\n"
                           f"<b>Current Mode:</b> {current_mode}\n"
                           f"<i>{current_desc}</i>\n\n"
                           f"When a user claims an account, notifications will be sent based on this setting.\n\n"
                           f"Select a notification mode:")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001F464 Admins Only" + (" \u2705" if current_mode == 'admins' else ""), callback_data="admin_set_notif_mode_admins")],
                [InlineKeyboardButton("\U0001F4E2 Log Channel Only" + (" \u2705" if current_mode == 'log_channel' else ""), callback_data="admin_set_notif_mode_log_channel")],
                [InlineKeyboardButton("\U0001F501 Both" + (" \u2705" if current_mode == 'both' else ""), callback_data="admin_set_notif_mode_both")],
                [InlineKeyboardButton("\U0001F515 Disabled" + (" \u2705" if current_mode == 'none' else ""), callback_data="admin_set_notif_mode_none")],
                [InlineKeyboardButton("\u2b05\ufe0f Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action.startswith("admin_set_notif_mode_"):
            new_mode = action.replace("admin_set_notif_mode_", "")
            valid_modes = ['admins', 'log_channel', 'both', 'none']
            if new_mode in valid_modes:
                bot_config['claim_notification_mode'] = new_mode
                save_config()
                mode_names = {'admins': 'Admins Only', 'log_channel': 'Log Channel Only', 'both': 'Both', 'none': 'Disabled'}
                message_text = f"\u2705 <b>Notification mode updated to: {mode_names.get(new_mode, new_mode)}</b>"
                
                # Warn if log_channel mode is set but no channel configured
                if new_mode in ['log_channel', 'both'] and not bot_config.get('log_channel'):
                    message_text += "\n\n\u26a0\ufe0f <b>Warning:</b> No log channel is configured! Please set one in Log Channel Settings."
            else:
                message_text = "\u274c <b>Invalid notification mode.</b>"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2b05\ufe0f Back to Claim Notifications", callback_data="admin_claim_notifications")]
            ])

        elif action == "admin_settings_points":
            message_text = (f"💰 <b>Points Configuration</b>\n\n"
                            f"1. Points per Referral: {bot_config.get('points_per_referral', 0)}\n"
                            f"2. Points Bonus per Account: {bot_config.get('points_per_account_bonus', 0)}\n"
                            f"3. Default Points on Join: {bot_config.get('default_points_on_join', 0)}\n\n"
                            "Select a setting to change or go back.")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Change Points/Referral", callback_data="admin_set_points_referral")],
                [InlineKeyboardButton("Change Points/Account Bonus", callback_data="admin_set_points_account")],
                [InlineKeyboardButton("Change Default Points on Join", callback_data="admin_set_points_join")],
                [InlineKeyboardButton("⬅️ Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_set_points_referral":
            context.user_data.update({'awaiting_admin_input': 'set_config_value', 'config_key_to_set': 'points_per_referral', 'config_description': 'Points per Referral', 'return_to_menu': 'admin_settings_points'})
            message_text = f"Enter new value for Points per Referral (current: {bot_config.get('points_per_referral',0)}).\nOr /cancel_admin"

        elif action == "admin_set_points_account":
            context.user_data.update({'awaiting_admin_input': 'set_config_value', 'config_key_to_set': 'points_per_account_bonus', 'config_description': 'Points Bonus per Account', 'return_to_menu': 'admin_settings_points'})
            message_text = f"Enter new value for Points Bonus per Account (current: {bot_config.get('points_per_account_bonus',0)}).\nOr /cancel_admin"

        elif action == "admin_set_points_join":
            context.user_data.update({'awaiting_admin_input': 'set_config_value', 'config_key_to_set': 'default_points_on_join', 'config_description': 'Default Points on Join', 'return_to_menu': 'admin_settings_points'})
            message_text = f"Enter new value for Default Points on Join (current: {bot_config.get('default_points_on_join',0)}).\nOr /cancel_admin"

        elif action == "admin_settings_key_validity":
            kv_conf = bot_config.get("key_validity_days", DEFAULT_BOT_CONFIG["key_validity_days"])
            message_text = (f"🔑 <b>Key Validity Durations (days)</b>\n\n"
                            f"Lifetime: {kv_conf.get('lifetime', 3650)}\n\n"
                            "Select to change lifetime validity or go back.")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Change Lifetime Validity", callback_data="admin_set_key_validity_lifetime")],
                [InlineKeyboardButton("⬅️ Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_set_key_validity_lifetime":
            context.user_data.update({'awaiting_admin_input': 'set_nested_config_value', 'config_key_to_set': ('key_validity_days', 'lifetime'), 'config_description': 'Lifetime Key Validity (days)', 'return_to_menu': 'admin_settings_key_validity'})
            current_val = bot_config.get("key_validity_days", {}).get('lifetime', 3650)
            message_text = f"Enter new value for Lifetime Key Validity (days) (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_settings_ban_limits":
            bd_conf = bot_config.get("ban_durations_seconds", DEFAULT_BOT_CONFIG["ban_durations_seconds"])
            rl_conf = bot_config.get("request_limits_per_10_min", DEFAULT_BOT_CONFIG["request_limits_per_10_min"])
            claim_lim = bot_config.get("claim_limit_hours", DEFAULT_BOT_CONFIG.get("claim_limit_hours", 24))
            req_window_sec = bot_config.get("request_window_seconds", DEFAULT_BOT_CONFIG.get("request_window_seconds", 600))
            req_window_min = int(req_window_sec // 60)
            message_text = (
                f"🚫 <b>Restriction & Request Limits</b>\n\n"
                f"<b>Restriction Durations (seconds):</b>\n"
                f" Normal User: {bd_conf.get('normal_user', 600)}\n"
                f" Premium User: {bd_conf.get('premium_user', 30)}\n\n"
                f"<b>Request Limits (per window):</b>\n"
                f" Normal User: {rl_conf.get('normal_user', 5)}\n"
                f" Premium User: {rl_conf.get('premium_user', 20)}\n"
                f" Window Length: {req_window_min} minute{'s' if req_window_min != 1 else ''}\n\n"
                f"<b>Claim Limit (Global):</b> {claim_lim} hour{'s' if claim_lim != 1 else ''}\n\n"
                f"<b>Max Claims Per User (per cooldown):</b>\n"
                f" Free User: {bot_config.get('max_claims_per_user', {}).get('free', 1)} account(s)\n"
                f" Paid User: {bot_config.get('max_claims_per_user', {}).get('paid', 2)} account(s)\n\n"
                f"<b>Temporary Ban Duration:</b>\n"
                f" Normal User: {bot_config.get('temp_ban_duration_seconds', {}).get('normal_user', 600)} second(s)\n"
                f" Premium User: {bot_config.get('temp_ban_duration_seconds', {}).get('premium_user', 30)} second(s)\n\n"
                "Select a setting to change or go back."
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Change Normal Restriction Duration", callback_data="admin_set_ban_normal")],
                [InlineKeyboardButton("Change Premium Restriction Duration", callback_data="admin_set_ban_premium")],
                [InlineKeyboardButton("Change Normal Request Limit", callback_data="admin_set_req_normal")],
                [InlineKeyboardButton("Change Premium Request Limit", callback_data="admin_set_req_premium")],
                [InlineKeyboardButton("Change Request Window (min)", callback_data="admin_set_request_window")],
                [InlineKeyboardButton("Change Claim Limit (hours)", callback_data="admin_set_claim_limit")],
                [InlineKeyboardButton("Set Temp Ban Time (Normal)", callback_data="admin_set_temp_ban_normal")],
                [InlineKeyboardButton("Set Temp Ban Time (Premium)", callback_data="admin_set_temp_ban_premium")],
                [InlineKeyboardButton("Set Max Claims (Free User)", callback_data="admin_set_max_claims_free")],
                [InlineKeyboardButton("Set Max Claims (Paid User)", callback_data="admin_set_max_claims_paid")],
                [InlineKeyboardButton("⬅️ Back to Bot Settings", callback_data="admin_bot_settings_menu")]
            ])

        elif action == "admin_set_ban_normal":
            context.user_data.update({'awaiting_admin_input': 'set_nested_config_value', 'config_key_to_set': ('ban_durations_seconds', 'normal_user'), 'config_description': 'Normal User Restriction Duration (seconds)', 'return_to_menu': 'admin_settings_ban_limits'})
            current_val = bot_config.get("ban_durations_seconds", {}).get('normal_user', 600)
            message_text = f"Enter new value for Normal User Restriction Duration (seconds) (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_ban_premium":
            context.user_data.update({'awaiting_admin_input': 'set_nested_config_value', 'config_key_to_set': ('ban_durations_seconds', 'premium_user'), 'config_description': 'Premium User Restriction Duration (seconds)', 'return_to_menu': 'admin_settings_ban_limits'})
            current_val = bot_config.get("ban_durations_seconds", {}).get('premium_user', 30)
            message_text = f"Enter new value for Premium User Restriction Duration (seconds) (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_req_normal":
            context.user_data.update({'awaiting_admin_input': 'set_nested_config_value', 'config_key_to_set': ('request_limits_per_10_min', 'normal_user'), 'config_description': 'Normal User Request Limit (per window)', 'return_to_menu': 'admin_settings_ban_limits'})
            current_val = bot_config.get("request_limits_per_10_min", {}).get('normal_user', 5)
            message_text = f"Enter new value for Normal User Request Limit (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_req_premium":
            context.user_data.update({'awaiting_admin_input': 'set_nested_config_value', 'config_key_to_set': ('request_limits_per_10_min', 'premium_user'), 'config_description': 'Premium User Request Limit (per window)', 'return_to_menu': 'admin_settings_ban_limits'})
            current_val = bot_config.get("request_limits_per_10_min", {}).get('premium_user', 20)
            message_text = f"Enter new value for Premium User Request Limit (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_request_window":
            # Admin is changing the request window length (in minutes). Use minutes to seconds conversion.
            context.user_data.update({
                'awaiting_admin_input': 'set_config_value',
                'config_key_to_set': 'request_window_seconds',
                'config_description': 'Request Window (minutes)',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            # Indicate that the provided value should be converted from minutes to seconds
            context.user_data['value_transform'] = 'minutes_to_seconds'
            current_val_min = int(bot_config.get('request_window_seconds', DEFAULT_BOT_CONFIG.get('request_window_seconds', 600)) // 60)
            message_text = f"Enter new value for Request Window (minutes) (current: {current_val_min}).\nOr /cancel_admin"

        elif action == "admin_set_claim_limit":
            # Admin is changing the global claim limit (hours)
            context.user_data.update({
                'awaiting_admin_input': 'set_config_value',
                'config_key_to_set': 'claim_limit_hours',
                'config_description': 'Claim Limit (hours)',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            current_val = bot_config.get('claim_limit_hours', DEFAULT_BOT_CONFIG.get('claim_limit_hours', 24))
            message_text = f"Enter new value for Claim Limit (hours) (current: {current_val}).\nOr /cancel_admin"

        # ==================== NEW HANDLERS FOR TEMP BAN TIME ====================
        elif action == "admin_set_temp_ban_normal":
            context.user_data.update({
                'awaiting_admin_input': 'set_nested_config_value',
                'config_key_to_set': ('temp_ban_duration_seconds', 'normal_user'),
                'config_description': 'Temporary Ban Duration for Normal Users (seconds)',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            current_val = bot_config.get("temp_ban_duration_seconds", {}).get('normal_user', 600)
            message_text = f"Enter new value for Temporary Ban Duration - Normal Users (seconds) (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_temp_ban_premium":
            context.user_data.update({
                'awaiting_admin_input': 'set_nested_config_value',
                'config_key_to_set': ('temp_ban_duration_seconds', 'premium_user'),
                'config_description': 'Temporary Ban Duration for Premium Users (seconds)',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            current_val = bot_config.get("temp_ban_duration_seconds", {}).get('premium_user', 300)
            message_text = f"Enter new value for Temporary Ban Duration - Premium Users (seconds) (current: {current_val}).\nOr /cancel_admin"

        # ==================== NEW HANDLERS FOR MAX CLAIMS ====================
        elif action == "admin_set_max_claims_free":
            context.user_data.update({
                'awaiting_admin_input': 'set_nested_config_value',
                'config_key_to_set': ('max_claims_per_day', 'standard'),
                'config_description': 'Maximum Claims Per Day - Standard Users',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            current_val = bot_config.get("max_claims_per_day", {}).get('standard', 5)
            message_text = f"Enter new value for Maximum Claims Per Day - Standard Users (current: {current_val}).\nOr /cancel_admin"

        elif action == "admin_set_max_claims_paid":
            context.user_data.update({
                'awaiting_admin_input': 'set_nested_config_value',
                'config_key_to_set': ('max_claims_per_day', 'premium'),
                'config_description': 'Maximum Claims Per Day - Premium Users',
                'return_to_menu': 'admin_settings_ban_limits'
            })
            current_val = bot_config.get("max_claims_per_day", {}).get('premium', 20)
            message_text = f"Enter new value for Maximum Claims Per Day - Premium Users (current: {current_val}).\nOr /cancel_admin"

        ## adding messing handells
        # ADD THESE BLOCKS BEFORE THE FINAL "else" IN admin_callback_router
        elif action.startswith("admin_points_adjust_"):
            user_id_to_adjust = action.replace("admin_points_adjust_", "")
            users_data = load_json(USERS_JSON_FILE)
            user_data = users_data.get(user_id_to_adjust, {})
            username_to_adjust = user_data.get('username', f"user{user_id_to_adjust}")
            context.user_data['awaiting_admin_input'] = 'adjust_points_for_user'
            context.user_data['target_user_id'] = user_id_to_adjust
            context.user_data['target_username'] = username_to_adjust
            context.user_data['return_to_menu'] = 'admin_panel_main'
            # Format username display to avoid '@' on fallback usernames
            username_display = format_username_display(username_to_adjust)
            message_text = (
                f"💰 <b>Adjust Points for {username_display}</b>\n\n"
                f"Enter points to add (+) or subtract (-):\n"
                f"<i>Example: +100 or -50</i>\n\n"
                f"Type /cancel_admin to cancel."
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Cancel", callback_data="admin_panel_main")]
            ])
            current_parse_mode = ParseMode.HTML
        # Handle selection of a specific service for full claim history
        elif action.startswith("admin_service_claims_"):
            # Extract service key from the callback data and display claim history
            service_key = action.replace("admin_service_claims_", "", 1)
            context.user_data['service_claims_service'] = service_key
            context.user_data['service_claims_page'] = 0
            await admin_view_service_claims(update, context)
            return

        elif action.startswith("admin_grant_premium_"):
            user_id_to_grant = action.replace("admin_grant_premium_", "")
            users_data = load_json(USERS_JSON_FILE)
            user_data = users_data.get(user_id_to_grant, {})
            username_to_grant = user_data.get('username', f"user{user_id_to_grant}")
            context.user_data['awaiting_admin_input'] = 'grant_premium_for_user'
            context.user_data['target_user_id'] = user_id_to_grant
            context.user_data['target_username'] = username_to_grant
            context.user_data['return_to_menu'] = 'admin_panel_main'
            # Format username display to avoid '@' on fallback usernames
            username_display = format_username_display(username_to_grant)
            message_text = (
                f"💎 <b>Grant Premium for {username_display}</b>\n\n"
                f"Enter days of premium access:\n"
                f"<i>Example: 30 (or 3650 for lifetime)</i>\n\n"
                f"Type /cancel_admin to cancel."
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Cancel", callback_data="admin_panel_main")]
            ])
            current_parse_mode = ParseMode.HTML

        elif action == "admin_recent_claims":
            all_claims = get_all_claims(limit=15)
            if not all_claims:
                message_text = "📭 <b>No Recent Claims</b>\nNo claims found in database."
            else:
                message_text = "📦 <b>Recent Claims (Last 15)</b>\n"
                for i, claim in enumerate(all_claims, 1):
                    user_id = claim.get('user_id', 'Unknown')
                    user_info = load_json(USERS_JSON_FILE).get(user_id, {})
                    username = user_info.get('username', f"user{user_id}")
                    service_key = claim.get('service_key', 'Unknown')
                    service_name = services.get(service_key, {}).get('text', service_key)
                    timestamp = claim.get('timestamp', 'Unknown')[:16]  # Truncate to YYYY-MM-DD HH:MM
                    # Build clickable username link when possible
                    if user_id != 'Unknown':
                        username_display = format_user_link(user_id, username)
                    else:
                        username_display = format_username_display(username)
                    message_text += f"{i}. {username_display} | {service_name} | {timestamp}\n"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_view_claimed_accounts")]
            ])
            current_parse_mode = ParseMode.HTML

        elif action == "admin_claims_stats":
            all_claims = get_all_claims(limit=1000)
            total_claims = len(all_claims)
            user_claims = {}
            service_claims = {}

            for claim in all_claims:
                uid = claim.get('user_id', 'unknown')
                sk = claim.get('service_key', 'unknown')
                user_claims[uid] = user_claims.get(uid, 0) + 1
                service_claims[sk] = service_claims.get(sk, 0) + 1

            # Top users
            top_users = sorted(user_claims.items(), key=lambda x: x[1], reverse=True)[:5]
            top_users_text = "\n".join([
                f"• {format_username_display(load_json(USERS_JSON_FILE).get(uid, {}).get('username', f'user{uid}'))}: {count} claims"
                for uid, count in top_users
            ]) if top_users else "No data"

            # Top services
            top_services = sorted(service_claims.items(), key=lambda x: x[1], reverse=True)[:5]
            top_services_text = "\n".join([
                f"• {services.get(sk, {}).get('text', sk)}: {count} claims"
                for sk, count in top_services
            ]) if top_services else "No data"

            message_text = (
                f"📈 <b>Claims Statistics</b>\n\n"
                f"📊 <b>Total Claims:</b> {total_claims}\n"
                f"👥 <b>Unique Users:</b> {len(user_claims)}\n"
                f"📱 <b>Services Used:</b> {len(service_claims)}\n\n"
                f"🏆 <b>Top Claimers:</b>\n{top_users_text}\n\n"
                f"📦 <b>Most Claimed Services:</b>\n{top_services_text}"
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Claims", callback_data="admin_view_claimed_accounts")]
            ])
            current_parse_mode = ParseMode.HTML






        elif action == "admin_view_all_members":
            await admin_view_all_members(update, context)
            return

        elif action == "admin_view_referral_stats":
            await admin_view_referral_stats(update, context)
            return

        elif action == "admin_view_claimed_accounts":
            await admin_view_claimed_accounts(update, context)
            return

        elif action == "admin_export_members":
            await admin_export_members(update, context)
            return

        elif action == "admin_export_referral_stats":
            await admin_export_referral_stats(update, context)
            return

        elif action == "admin_view_all_members_interactive" or action.startswith("admin_view_unbanned_page_"):
            await admin_view_all_members_interactive(update, context)
            return

        elif action == "admin_view_all_members_interactive":
            await admin_view_all_members_interactive(update, context)
            return

        elif action.startswith("admin_manage_member_"):
            target_id = action.replace("admin_manage_member_", "")
            await admin_manage_member_options(update, context, target_id)
            return

        elif action == "admin_view_premium_users":
            await admin_view_premium_users(update, context)
            return

        elif action.startswith("admin_manage_prem_"):
            target_uid = action.replace("admin_manage_prem_", "")
            await admin_manage_premium_user_options(update, context, target_uid)
            return

        elif action.startswith("admin_remove_prem_confirm_"):
            target_uid = action.replace("admin_remove_prem_confirm_", "")
            users_data = load_json(USERS_JSON_FILE)
            username = users_data.get(target_uid, {}).get('username', f"user{target_uid}")
            if update_user_premium_status(target_uid, username, "remove"):
                await query.answer(f"✅ Premium removed for {username}", show_alert=True)
            else:
                await query.answer("❌ Failed to remove premium", show_alert=True)
            await admin_view_premium_users(update, context)
            return

        elif action == "admin_search_user":
            await admin_search_user_menu(update, context)
            return

        elif action == "admin_recalculate_points":
            await admin_recalculate_points(update, context)
            return

        elif action == "admin_transfer_stats":
            await admin_transfer_stats(update, context)
            return

        elif action == "admin_redemption_stats":
            await admin_redemption_stats(update, context)
            return

        elif action == "admin_view_specific_user_claims":
            await admin_view_specific_user_claims(update, context)
            return

        elif action == "admin_live_stocks":
            await admin_live_stocks(update, context)
            return

        elif action == "admin_view_perm_banned":
            await admin_view_perm_banned(update, context)
            return

        elif action == "admin_ban_stats":
            await admin_ban_stats(update, context)
            return

        elif action == "user_live_stocks":
            await user_live_stocks(update, context)
            return

        elif action == "admin_check_user_points_calculation":
            await admin_check_user_points_calculation(update, context)
            return

        # ==================== UNBAN ALL USERS HANDLER ====================
        elif action == "admin_unban_all_users":
            await unban_all_permanently_banned(update, context)
            return

        # ==================== TEMPORARY BAN TIME HANDLERS ====================

        else:
            if not message_text:
                logger.warning(f"Unhandled admin action in router: {action}")
                message_text = "This admin function is under development or unrecognized."
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]])

        if query and query.message:
            await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode=current_parse_mode)
        else:
            await safe_send_message(user.id, message_text, context, reply_markup=reply_markup, parse_mode=current_parse_mode)

    except BadRequest as e:
        logger.error(f"BadRequest in admin_callback_router for action {action}: {e}. Text: '{message_text[:200]}'")
        if query and query.message and "Message can't be edited" in str(e):
            await safe_send_message(query.message.chat_id, message_text, context, reply_markup=reply_markup, parse_mode=current_parse_mode)
        elif query and query.message:
            try:
                await query.message.edit_text(f"An error occurred processing: {action}. Please try again or check logs.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]]),
                                            parse_mode=ParseMode.HTML)
            except Exception as e_fallback:
                logger.error(f"Failed to send fallback message for admin_callback_router: {e_fallback}")
    except Exception as e:
        logger.error(f"Generic error in admin_callback_router for action {action}: {e}", exc_info=True)
        if query and query.message:
            try:
                await query.message.edit_text("An unexpected error occurred.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_panel_main")]]),
                                            parse_mode=ParseMode.HTML)
            except Exception:
                pass

async def handle_admin_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    if 'awaiting_feedback_reply' in context.user_data:
        await handle_feedback_reply(update, context)
        return

    if context.user_data.get('awaiting_feedback'):
        await send_feedback_message(update, context)
        return

    if context.user_data.get('awaiting_search'):
        await handle_search_input(update, context)
        return

    if not (is_admin(user.id) and 'awaiting_admin_input' in context.user_data):
        return

    state = context.user_data.pop('awaiting_admin_input')
    text_input = update.message.text if update.message and update.message.text else None
    doc = update.message.document if update.message and update.message.document else None
    photo = update.message.photo if update.message and update.message.photo else None
    video = update.message.video if update.message and update.message.video else None
    caption = update.message.caption if update.message and update.message.caption else None

    return_to_menu_cb_data = context.user_data.pop('return_to_menu', 'admin_panel_main')
    response_message = ""
    response_parse_mode = ParseMode.HTML

    if state == 'add_admin_id':
        if not text_input:
            response_message = "No User ID provided."
        else:
            try:
                new_admin_id = int(text_input.strip())
                if new_admin_id not in admin_user_ids:
                    admin_user_ids.add(new_admin_id)
                    save_admins()
                    response_message = f"✅ Admin ID {new_admin_id} added successfully."
                else:
                    response_message = f"ℹ️ Admin ID {new_admin_id} is already an admin."
            except ValueError:
                response_message = "❌ Invalid User ID format. Please send a numeric ID."
            except Exception as e:
                logger.error(f"Error adding admin ID: {e}")
                response_message = "❌ An error occurred."

    elif state == 'upload_data':
        # Check if message contains a document
        if not update.message.document:
            await update.message.reply_text(
                "❌ Please upload a ZIP file. Send a ZIP file or type /cancel_admin to cancel.",
                parse_mode=ParseMode.HTML
            )
            # Keep the state active
            context.user_data['awaiting_admin_input'] = 'upload_data'
            return

        document = update.message.document
        if not document.file_name or not document.file_name.endswith('.zip'):
            await update.message.reply_text("❌ Please upload a ZIP file.")
            # Keep the state active
            context.user_data['awaiting_admin_input'] = 'upload_data'
            return

        # Process the ZIP file
        await process_zip_file(update, context, document)

        # Clear the state
        if 'awaiting_admin_input' in context.user_data:
            del context.user_data['awaiting_admin_input']
        return

    elif state == 'remove_admin_id':
        if not text_input:
            response_message = "No User ID provided."
        else:
            try:
                admin_id_to_remove = int(text_input.strip())
                if user.id == admin_id_to_remove:
                    response_message = "❌ You cannot remove yourself as an admin this way."
                elif admin_id_to_remove in admin_user_ids:
                    admin_user_ids.remove(admin_id_to_remove)
                    save_admins()
                    response_message = f"✅ Admin ID {admin_id_to_remove} removed."
                else:
                    response_message = f"❌ Admin ID {admin_id_to_remove} not found."
            except ValueError:
                response_message = "❌ Invalid User ID format."
            except Exception as e:
                logger.error(f"Error removing admin ID: {e}")
                response_message = "❌ An error occurred."

    elif state == 'set_config_value':
        config_key = context.user_data.pop('config_key_to_set', None)
        description = context.user_data.pop('config_description', 'Setting')

        if config_key and text_input:
            try:
                new_value = int(text_input.strip())
                # Check if we need to transform the value (e.g., minutes to seconds)
                value_transform = context.user_data.pop('value_transform', None)
                if value_transform == 'minutes_to_seconds':
                    new_value = new_value * 60
                bot_config[config_key] = new_value
                save_config()
                response_message = f"✅ {description} updated to {new_value}."
            except ValueError:
                response_message = "❌ Invalid value. Please enter a number."
            except Exception as e:
                logger.error(f"Error setting config value for {config_key}: {e}")
                response_message = f"❌ Error updating {description}."
        elif not text_input:
            response_message = "No value provided."


    # ADD THESE BLOCKS IN handle_admin_message_input AFTER existing state handlers

        elif state == 'check_user_points_calculation':
            if not text_input:
                response_message = "No username or ID provided."
            else:
                user_id_to_check = None
                username_to_check = None

                if text_input.isdigit():
                    user_id_to_check = text_input
                    users_data = load_json(USERS_JSON_FILE)
                    user_data = users_data.get(user_id_to_check, {})
                    username_to_check = user_data.get('username')
                    if not username_to_check:
                        username_to_check = f"user{user_id_to_check}"
                else:
                    username_to_check = text_input.lstrip('@')
                    users_data = load_json(USERS_JSON_FILE)
                    for uid, data in users_data.items():
                        if data.get('username') == username_to_check:
                            user_id_to_check = uid
                            break

                if not user_id_to_check:
                    response_message = f"❌ User '{text_input}' not found."
                else:
                    # Get detailed calculation
                    breakdown = calculate_user_points_breakdown(user_id_to_check)

                    if 'error' in breakdown:
                        response_message = f"❌ Error: {breakdown['error']}"
                    else:
                        # Format detailed response
                        response_message = (
                            f"🧮 <b>Point Calculation for {format_username_display(breakdown['username'])}</b>\n"
                            f"User ID: <code>{user_id_to_check}</code>\n\n"

                            f"<b>Current Points:</b> {get_user_points(user_id_to_check)}\n"
                            f"<b>Calculated Points:</b> {breakdown['expected_points']}\n"
                            f"<b>Difference:</b> {breakdown['expected_points'] - get_user_points(user_id_to_check):+d}\n\n"

                            f"<b>📊 Detailed Breakdown:</b>\n"
                            f"• Referrals: {breakdown['breakdown']['referral_count']} × {breakdown['breakdown']['points_per_referral']} = +{breakdown['breakdown']['points_from_referrals']}\n"
                            f"• Default on join: +{breakdown['breakdown']['default_points_on_join']}\n"
                            f"• Total spent on services: -{breakdown['breakdown']['total_spent']}\n"
                            f"• Points from key redemptions: +{breakdown['breakdown']['total_redeemed']}\n"
                            f"• Net transfers: {breakdown['breakdown']['net_transfers']:+d}\n\n"

                            f"<b>📝 Formula:</b> {breakdown['breakdown']['formula']}\n\n"
                        )

                        # Add purchase details
                        if breakdown['breakdown']['purchases']:
                            response_message += f"<b>🛒 Service Purchases:</b>\n"
                            for purchase in breakdown['breakdown']['purchases']:
                                service_name = purchase['service_key']
                                if purchase['service_key'] in services:
                                    service_info = services[purchase['service_key']]
                                    if isinstance(service_info, dict):
                                        service_name = service_info.get('text', purchase['service_key'])
                                    else:
                                        service_name = service_info
                                response_message += f"• {service_name}: {purchase['count']}×{purchase['price_per_item']} = {purchase['total_spent']} pts\n"

                        # Add note about recalculation
                        response_message += f"\n<i>Use 'Recalc Points Based on Referrals' in Points Management to update if needed.</i>"


        elif state == 'check_user_points':
            if not text_input:
                response_message = "No username or ID provided."
        else:
            user_id_to_check = None
            username_to_check = None

            if text_input.isdigit():
                user_id_to_check = text_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_check, {})
                username_to_check = user_data.get('username')
                if not username_to_check:
                    username_to_check = f"user{user_id_to_check}"
            else:
                username_to_check = text_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == username_to_check:
                        user_id_to_check = uid
                        break

            if not user_id_to_check:
                response_message = f"❌ User '{text_input}' not found."
            else:
                # Get detailed calculation
                calculation = check_user_points_calculation(user_id_to_check)

                if 'error' in calculation:
                    response_message = f"❌ Error: {calculation['error']}"
                else:
                    # Format detailed response
                    response_message = (
                        f"🧮 <b>Point Calculation for {format_username_display(calculation['username'])}</b>\n"
                        f"User ID: <code>{calculation['user_id']}</code>\n\n"

                        f"<b>Current Points:</b> {calculation['current_points']}\n"
                        f"<b>Expected Points:</b> {calculation['expected_points']}\n"
                        f"<b>Difference:</b> {calculation['difference']:+d}\n\n"

                        f"<b>Calculation Breakdown:</b>\n"
                        f"• Referrals: {calculation['calculation']['referral_count']} × {calculation['calculation']['points_per_referral']} = +{calculation['calculation']['points_from_referrals']}\n"
                        f"• Default on join: +{calculation['calculation']['default_points_on_join']}\n"
                        f"• Total spent: -{calculation['calculation']['total_spent']}\n\n"
                        f"<b>Formula:</b> {calculation['calculation']['formula']}\n\n"
                    )

                    # Add purchase details
                    if calculation['calculation']['purchases']:
                        response_message += f"<b>Purchases:</b>\n"
                        for purchase in calculation['calculation']['purchases']:
                            service_name = purchase['service_key']
                            if purchase['service_key'] in services:
                                service_name = services[purchase['service_key']].get('text', purchase['service_key'])
                            response_message += f"• {service_name}: {purchase['count']}×{purchase['price_per_item']} = {purchase['total_spent']} pts\n"

    elif state == 'adjust_points_for_user':
        target_user_id = context.user_data.get('target_user_id')
        target_username = context.user_data.get('target_username')
        if not text_input:
            response_message = "❌ No value provided. Operation cancelled."
        else:
            try:
                points_change = int(text_input.strip())
                if update_user_points(target_user_id, target_username, points_change):
                    new_total = get_user_points(target_user_id)
                    username_display = format_username_display(target_username)
                    response_message = (
                        f"✅ <b>Points Adjusted!</b>\n"
                        f"👤 User: {username_display} (ID: {target_user_id})\n"
                        f"💰 Change: {points_change:+d}\n"
                        f"✨ New Balance: {new_total}"
                    )
                else:
                    response_message = f"❌ Failed to update points for {format_username_display(target_username)}."
            except ValueError:
                response_message = "❌ Invalid format. Enter a number (e.g., +100 or -50)."
            except Exception as e:
                logger.error(f"Error adjusting points for {target_user_id}: {e}")
                response_message = "❌ An error occurred during adjustment."
        # Clean up context
        context.user_data.pop('target_user_id', None)
        context.user_data.pop('target_username', None)

    elif state == 'grant_premium_for_user':
        target_user_id = context.user_data.get('target_user_id')
        target_username = context.user_data.get('target_username')
        if not text_input:
            response_message = "❌ No value provided. Operation cancelled."
        else:
            try:
                days = int(text_input.strip())
                if days <= 0:
                    response_message = "❌ Days must be positive number."
                else:
                    key_type = "lifetime" if days >= 3650 else "standard"
                if update_user_premium_status(target_user_id, target_username, key_type, days_override=days):
                    expiry = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime('%Y-%m-%d')
                    username_display = format_username_display(target_username)
                    response_message = (
                        f"✅ <b>Premium Granted!</b>\n"
                        f"👤 User: {username_display} (ID: {target_user_id})\n"
                        f"💎 Duration: {days} days\n"
                        f"📅 Expires: {expiry}"
                    )
                else:
                    response_message = f"❌ Failed to grant premium to {format_username_display(target_username)}."
            except ValueError:
                response_message = "❌ Invalid format. Enter number of days (e.g., 30)."
            except Exception as e:
                logger.error(f"Error granting premium to {target_user_id}: {e}")
                response_message = "❌ An error occurred during grant."
        # Clean up context
        context.user_data.pop('target_user_id', None)
        context.user_data.pop('target_username', None)


    elif state == 'set_nested_config_value':
        keys_tuple = context.user_data.pop('config_key_to_set', None)
        description = context.user_data.pop('config_description', 'Setting')

        if keys_tuple and len(keys_tuple) == 2 and text_input:
            main_key, sub_key = keys_tuple
            try:
                new_value = int(text_input.strip())
                if main_key not in bot_config or not isinstance(bot_config[main_key], dict):
                    bot_config[main_key] = {}
                bot_config[main_key][sub_key] = new_value
                save_config()
                response_message = f"✅ {description} updated to {new_value}."
            except ValueError:
                response_message = "❌ Invalid value. Please enter a number."
            except Exception as e:
                logger.error(f"Error setting nested config for {main_key}/{sub_key}: {e}")
                response_message = f"❌ Error updating {description}."
        elif not text_input:
            response_message = "No value provided."

    elif state == 'add_service':
        service_key, service_name, storage_type, path_str_rel, category_str = "", "", "", "", "Other"
        accounts_to_add = []

        try:
            if doc:
                if doc.file_name.lower().endswith(".txt"):
                    temp_upload_dir = TEMP_DIR / "uploads"
                    temp_upload_dir.mkdir(parents=True, exist_ok=True)
                    file_obj = await doc.get_file()
                    dl_path = temp_upload_dir / doc.file_name
                    await file_obj.download_to_drive(dl_path)

                    with open(dl_path, 'r', encoding='utf-8') as f_serv:
                        lines = [line.strip() for line in f_serv if line.strip()]

                    if lines:
                        config_parts = lines[0].split('|')
                        if len(config_parts) >= 5:
                            service_key, service_name, storage_type, path_str_rel, category_str = [p.strip() for p in config_parts[:5]]
                        accounts_to_add = lines[1:]
                    else:
                        response_message = "Uploaded service file is empty."
                        raise ValueError("Empty file")

                    Path(dl_path).unlink(missing_ok=True)
                else:
                    response_message = "Please upload a .txt file for service definition."
                    raise ValueError("Wrong file type")
            elif text_input:
                config_parts = text_input.split('|')
                if len(config_parts) >= 5:
                    service_key, service_name, storage_type, path_str_rel, category_str = [p.strip() for p in config_parts[:5]]
                else:
                    response_message = "Format: key|Name|file_or_folder|path_relative|Category"
                    raise ValueError("Wrong format")
            else:
                response_message = "No input received for adding service."
                raise ValueError("No input")

            service_key = service_key.strip()
            if not service_key:
                response_message = "Service key cannot be empty."
                raise ValueError("Empty key")

            if service_key in services:
                response_message = f"Service key '{service_key}' already exists."
                raise ValueError("Key exists")

            default_bg = get_safe_background() or str(BASE_DIR / bot_config.get(BACKGROUND_IMAGE_PATH_CONFIG_KEY, DEFAULT_BACKGROUND_IMAGE_FILENAME))
            new_service_data = {'text': service_name, 'category': category_str or 'Other', 'backgrounds': [default_bg], 'from_admin_add': True}

            if storage_type.lower() == 'file' or storage_type.lower() == 'folder':
                new_service_data[storage_type.lower()] = path_str_rel
            else:
                response_message = "Invalid storage_type. Use 'file' or 'folder'."
                raise ValueError("Invalid storage type")

            services[service_key] = new_service_data
            save_services()
            add_accounts_to_service(service_key, accounts_to_add)
            update_service_price(service_key, 100)
            response_message = f"✅ Service '{service_name}' added. {len(accounts_to_add)} initial items processed."

        except ValueError as ve:
            if not response_message:
                response_message = f"Error: {str(ve)}"
        except Exception as e_add_serv:
            logger.error(f"Error adding service: {e_add_serv}", exc_info=True)
            response_message = f"Error adding service: {str(e_add_serv)}"

    elif state == 'select_service_for_accounts':
        if not text_input:
            response_message = "No service key provided."
        else:
            service_key_to_add_to = text_input.strip()
            if service_key_to_add_to in services:
                context.user_data['awaiting_admin_input'] = 'add_accounts_to_selected_service'
                context.user_data['service_for_accounts'] = service_key_to_add_to
                context.user_data['return_to_menu'] = 'admin_services_add_items_filter_menu'

                await safe_send_message(user.id, f"Now send the accounts/cookies for '{service_key_to_add_to}'.\n"
                                                "You can send:\n"
                                                " • A single .txt file (each line is an item).\n"
                                                " • A .zip file containing multiple .txt files.\n"
                                                " • Text pasted directly (each line is an item).\n"
                                                "Or /cancel_admin",
                                      context, parse_mode=ParseMode.HTML)
                return
            else:
                response_message = f"Service key '{service_key_to_add_to}' not found."

    elif state == 'add_accounts_to_selected_service':
        service_key = context.user_data.pop('service_for_accounts', None)
        if not service_key:
            response_message = "Error: Service context lost. Please start over."
        else:
            s_info = services.get(service_key)
            if not s_info:
                response_message = "Error: Service information not found."
            else:
                added_count = 0
                temp_upload_path = TEMP_DIR / "uploads" / f"{service_key}_{int(time.time())}"
                temp_upload_path.mkdir(parents=True, exist_ok=True)

                try:
                    if doc:
                        filename_lower = doc.file_name.lower()
                        downloaded_doc_path = temp_upload_path / doc.file_name
                        file_obj = await context.bot.get_file(doc.file_id)
                        await file_obj.download_to_drive(custom_path=str(downloaded_doc_path))

                        if filename_lower.endswith(".zip"):
                            temp_extract_dir = temp_upload_path / "extracted"
                            temp_extract_dir.mkdir(parents=True, exist_ok=True)

                            try:
                                with zipfile.ZipFile(downloaded_doc_path, 'r') as zip_ref:
                                    zip_ref.extractall(temp_extract_dir)

                                contents = []
                                for item in temp_extract_dir.rglob('*'):
                                    if item.is_file() and (item.suffix.lower() == '.txt' or not item.suffix):
                                        try:
                                            # Read file and split into lines (each line is an account)
                                            file_content = item.read_text(encoding='utf-8')
                                            lines = [line.strip() for line in file_content.split('\n') if line.strip()]
                                            for line in lines:
                                                contents.append(line)
                                                added_count += 1
                                        except Exception as e_read_item:
                                            logger.error(f"Error reading extracted item {item.name}: {e_read_item}")

                                add_accounts_to_service(service_key, contents)
                                response_message = f"Processed ZIP file. Added {added_count} items to '{s_info['text']}'."

                            except zipfile.BadZipFile:
                                response_message = "❌ Invalid ZIP file."
                            except Exception as e_zip:
                                logger.error(f"Error processing ZIP for {service_key}: {e_zip}")
                                response_message = f"❌ Error processing ZIP: {str(e_zip)}"
                            finally:
                                shutil.rmtree(temp_extract_dir, ignore_errors=True)

                        elif filename_lower.endswith(".txt"):
                            accounts_to_process = []
                            try:
                                # Try reading with utf-8 first, fallback to latin-1
                                try:
                                    file_text = downloaded_doc_path.read_text(encoding='utf-8')
                                except UnicodeDecodeError:
                                    file_text = downloaded_doc_path.read_text(encoding='latin-1')
                                
                                accounts_to_process = [line.strip() for line in file_text.split('\n') if line.strip()]
                            except Exception as e_txt:
                                logger.error(f"Error reading TXT file: {e_txt}")
                                response_message = f"❌ Error reading file: {str(e_txt)}"

                            if accounts_to_process:
                                add_accounts_to_service(service_key, accounts_to_process)
                            added_count = len(accounts_to_process)
                            if added_count > 0:
                                response_message = f"✅ Added {added_count} items from TXT to '{s_info['text']}'."

                        else:
                            response_message = "Unsupported file type. Please upload a .txt or .zip file."

                        downloaded_doc_path.unlink(missing_ok=True)

                    elif text_input:
                        accounts_to_process = [line.strip() for line in text_input.split('\n') if line.strip()]
                        add_accounts_to_service(service_key, accounts_to_process)
                        added_count = len(accounts_to_process)
                        if added_count > 0:
                            response_message = f"✅ Added {added_count} pasted items to '{s_info['text']}'."

                    if added_count == 0 and not (doc and doc.file_name.lower().endswith(".zip")):
                        if not response_message:
                            response_message = f"⚠️ No items were added to '{s_info['text']}'. Ensure format is correct."

                finally:
                    shutil.rmtree(temp_upload_path, ignore_errors=True)

    elif state == 'remove_service_items':
        if not text_input:
            response_message = "No service key provided."
        else:
            service_key_to_clear = text_input.strip()
            if service_key_to_clear in services:
                item_count = remove_all_items_from_service(service_key_to_clear)
                response_message = f"✅ Removed all {item_count} items from service '{service_key_to_clear}'."
            else:
                response_message = f"Service key '{service_key_to_clear}' not found."

    elif state == 'remove_service':
        if not text_input:
            response_message = "No service key provided."
        else:
            service_key_to_remove = text_input.strip()
            if service_key_to_remove in services:
                removed_info = services.pop(service_key_to_remove)

                services_data = load_json(SERVICES_FILE)
                if service_key_to_remove in services_data:
                    del services_data[service_key_to_remove]
                    save_json(SERVICES_FILE, services_data)

                prices_data = load_json(SERVICE_PRICES_FILE)
                if service_key_to_remove in prices_data:
                    del prices_data[service_key_to_remove]
                    save_json(SERVICE_PRICES_FILE, prices_data)

                service_items_data = load_json(SERVICE_ITEMS_FILE)
                if service_key_to_remove in service_items_data:
                    del service_items_data[service_key_to_remove]
                    save_json(SERVICE_ITEMS_FILE, service_items_data)

                response_message = f"✅ Service '{service_key_to_remove}' removed."
            else:
                response_message = f"Service key '{service_key_to_remove}' not found."

    elif state == 'generate_keys':
        if not text_input:
            response_message = "No parameters provided."
        else:
            try:
                parts = text_input.split()
                if len(parts) != 1:
                    raise ValueError("Expected format: count")

                count = int(parts[0])

                if not (1 <= count <= 100):
                    response_message = "Count must be between 1 and 100."
                    raise ValueError("Invalid count")

                generated_keys = [generate_key(user.id, "lifetime") for _ in range(count)]
                keys_str = "\n".join([f"`{k}`" for k in generated_keys if k])

                if keys_str:
                    response_message = f"🔑 Generated {count} lifetime keys:\n{keys_str}"
                else:
                    response_message = "Failed to generate keys."

            except ValueError as ve:
                if not response_message:
                    response_message = f"Format error: {str(ve)}. Use `count`."
            except Exception as e_genk:
                logger.error(f"Error generating keys: {e_genk}")
                response_message = f"Error: {str(e_genk)}"

    elif state == 'generate_point_keys':
        if not text_input:
            response_message = "No parameters provided."
        else:
            try:
                parts = text_input.split()
                if len(parts) != 2:
                    raise ValueError("Expected format: count points")

                count = int(parts[0])
                points = int(parts[1])

                if not (1 <= count <= 100):
                    response_message = "Count must be between 1 and 100."
                    raise ValueError("Invalid count")

                generated_keys = [generate_point_key(user.id, points) for _ in range(count)]
                keys_str = "\n".join([f"`{k}`" for k in generated_keys if k])

                if keys_str:
                    response_message = f"🔑 Generated {count} point keys each for {points} points:\n{keys_str}"
                else:
                    response_message = "Failed to generate point keys."

            except ValueError as ve:
                if not response_message:
                    response_message = f"Format error: {str(ve)}. Use `count points`."
            except Exception as e_genp:
                logger.error(f"Error generating point keys: {e_genp}")
                response_message = f"Error: {str(e_genp)}"

    elif state == 'add_channel':
        if not text_input and not update.message.forward_from_chat:
            response_message = "Please forward a message from the channel or provide channel info."
        else:
            try:
                if update.message.forward_from_chat:
                    channel = update.message.forward_from_chat
                    ch_username = channel.username
                    ch_chat_id = channel.id

                    if not ch_username:
                        ch_username = f"Private Channel {ch_chat_id}"

                    channels = bot_config.get("channels", [])
                    if any(ch.get('chat_id') == ch_chat_id for ch in channels):
                        response_message = "Channel already exists."
                    else:
                        invite_link = f"https://t.me/{ch_username}" if ch_username.startswith('@') else f"t.me/{ch_username}"
                        channels.append({"username": ch_username, "chat_id": ch_chat_id, "invite_link": invite_link})
                        bot_config["channels"] = channels
                        save_config()
                        response_message = f"✅ Channel '{ch_username}' (ID: {ch_chat_id}) added."

                elif text_input:
                    parts = text_input.split()
                    if len(parts) >= 2:
                        ch_username = parts[0].lstrip('@')
                        ch_chat_id = int(parts[1])

                        channels = bot_config.get("channels", [])
                        if any(ch.get('chat_id') == ch_chat_id for ch in channels):
                            response_message = "Channel already exists."
                        else:
                            invite_link = f"https://t.me/{ch_username}" if ch_username.startswith('@') else f"t.me/{ch_username}"
                            channels.append({"username": ch_username, "chat_id": ch_chat_id, "invite_link": invite_link})
                            bot_config["channels"] = channels
                            save_config()
                            response_message = f"✅ Channel '{ch_username}' (ID: {ch_chat_id}) added."
                    else:
                        response_message = "Format: @username chat_id (e.g., @mychannel -1001234567890)"

            except ValueError as ve:
                response_message = f"Format error: {str(ve)}. Use @username chat_id"
            except Exception as e_addch:
                logger.error(f"Error adding channel: {e_addch}")
                response_message = "An error occurred."

    elif state == 'set_log_channel':
        if not text_input and not update.message.forward_from_chat:
            response_message = "Please forward a message from the channel or provide channel info."
        else:
            try:
                if update.message.forward_from_chat:
                    channel = update.message.forward_from_chat
                    ch_username = f"@{channel.username}" if channel.username else f"Private Channel"
                    ch_chat_id = channel.id

                    bot_config['log_channel'] = {
                        'username': ch_username,
                        'chat_id': ch_chat_id
                    }
                    save_config()
                    response_message = f"\u2705 Log channel set to '{ch_username}' (ID: {ch_chat_id})"

                elif text_input:
                    # Try to parse as just a chat ID first
                    text_input = text_input.strip()
                    if text_input.lstrip('-').isdigit():
                        ch_chat_id = int(text_input)
                        ch_username = "Log Channel"
                    else:
                        # Parse as @username chat_id
                        parts = text_input.split()
                        if len(parts) >= 2:
                            ch_username = parts[0] if parts[0].startswith('@') else f"@{parts[0]}"
                            ch_chat_id = int(parts[1])
                        else:
                            response_message = "Format: @username chat_id or just chat_id"
                            raise ValueError("Invalid format")

                    bot_config['log_channel'] = {
                        'username': ch_username,
                        'chat_id': ch_chat_id
                    }
                    save_config()
                    response_message = f"\u2705 Log channel set to '{ch_username}' (ID: {ch_chat_id})"

            except ValueError as ve:
                if not response_message:
                    response_message = f"Format error: {str(ve)}. Use @username chat_id or just chat_id"
            except Exception as e_setlog:
                logger.error(f"Error setting log channel: {e_setlog}")
                response_message = "An error occurred while setting log channel."

    elif state == 'ban_user_manual' or state == 'unban_user_manual':
        if not text_input:
            response_message = "No username or ID provided."
        else:
            target_user_input = text_input.strip()
            target_user_id_found = None
            target_username_found = None

            if target_user_input.isdigit():
                target_user_id_found = target_user_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(target_user_id_found, {})
                target_username_found = user_data.get('username')
                if not target_username_found:
                    target_username_found = f"user{target_user_id_found}"
            else:
                target_username_found = target_user_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == target_username_found:
                        target_user_id_found = uid
                        break

            is_unban = state == 'unban_user_manual'
            custom_duration = None
            if not is_unban:
                parts = target_user_input.split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    custom_duration = int(parts[-1])
                    # Re-evaluate target if duration was part of input
                    target_user_input_clean = " ".join(parts[:-1])
                    # (Simplified: assume first part is user, last is duration)
                    if target_user_input_clean.isdigit():
                        target_user_id_found = target_user_input_clean
                        user_data = load_json(USERS_JSON_FILE).get(target_user_id_found, {})
                        target_username_found = user_data.get('username', f"user{target_user_id_found}")
                    else:
                        target_username_found = target_user_input_clean.lstrip('@')
                        for uid, data in load_json(USERS_JSON_FILE).items():
                            if data.get('username') == target_username_found:
                                target_user_id_found = uid
                                break

            # Temporarily override config for this call if custom_duration is set
            original_durations = None
            if custom_duration is not None:
                original_durations = bot_config.get("ban_durations_seconds", {}).copy()
                bot_config["ban_durations_seconds"] = {"normal_user": custom_duration, "premium_user": custom_duration}

            op_successful = check_and_ban_user(target_username_found, user_id_for_premium_check=int(target_user_id_found) if target_user_id_found else None, unban=is_unban, manual=not is_unban)
            
            if original_durations:
                bot_config["ban_durations_seconds"] = original_durations

            if is_unban:
                if op_successful:
                    response_message = f"User {format_username_display(target_username_found)} unbanned."
                else:
                    response_message = f"User {format_username_display(target_username_found)} not found in ban list or error."
            else:
                if op_successful:
                    ban_duration = is_user_banned(target_username_found)
                    response_message = f"User {format_username_display(target_username_found)} banned for {ban_duration}s."
                else:
                    response_message = f"Could not ban {format_username_display(target_username_found)}. User might be admin, already banned, or an error occurred."

    elif state == 'grant_premium_manual':
        if not text_input:
            response_message = "No parameters provided."
        else:
            try:
                parts = text_input.split()
                if len(parts) != 2:
                    raise ValueError("Expected format: username_or_id days")

                target_user_input_str = parts[0]
                days_str = parts[1]
                days = int(days_str)

                target_user_id_found = None
                target_username_for_update = None

                if target_user_input_str.isdigit():
                    target_user_id_found = target_user_input_str
                    users_data = load_json(USERS_JSON_FILE)
                    user_data = users_data.get(target_user_id_found, {})
                    target_username_for_update = user_data.get('username')
                    if not target_username_for_update:
                        target_username_for_update = f"user{target_user_id_found}"
                else:
                    target_username_for_update = target_user_input_str.lstrip('@')
                    users_data = load_json(USERS_JSON_FILE)
                    for uid, data in users_data.items():
                        if data.get('username') == target_username_for_update:
                            target_user_id_found = uid
                            break

                if target_user_id_found:
                    if update_user_premium_status(target_user_id_found, target_username_for_update, "standard", days_override=days):
                        response_message = f"✅ Granted premium for {days} days to {format_username_display(target_username_for_update)} (ID: {target_user_id_found})."
                    else:
                        response_message = f"❌ Failed to update premium for {format_username_display(target_username_for_update)}."
                else:
                    response_message = f"❌ User '{target_user_input_str}' not found in database. Ensure they have started the bot."

            except ValueError as ve:
                response_message = f"Format error: {str(ve)}. Use `username_or_id days`."
            except Exception as e_prem:
                logger.error(f"Error granting premium: {e_prem}")
                response_message = f"Error: {str(e_prem)}"

    elif state == 'remove_premium_manual':
        if not text_input:
            response_message = "No username or ID provided."
        else:
            target_user_input = text_input.strip()
            target_user_id_found = None
            target_username_found = None

            if target_user_input.isdigit():
                target_user_id_found = target_user_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(target_user_id_found, {})
                target_username_found = user_data.get('username')
                if not target_username_found:
                    target_username_found = f"user{target_user_id_found}"
            else:
                target_username_found = target_user_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == target_username_found:
                        target_user_id_found = uid
                        break

            if target_user_id_found:
                if update_user_premium_status(target_user_id_found, target_username_found, "remove"):
                    response_message = f"✅ Premium removed for {format_username_display(target_username_found)} (ID: {target_user_id_found})."
                else:
                    response_message = f"❌ Failed to remove premium for {format_username_display(target_username_found)}."
            else:
                response_message = f"❌ User '{target_user_input}' not found."

    elif state == 'post_announcement':
        sent_to_any = False
        channels = bot_config.get("channels", [])
        response_parse_mode = ParseMode.HTML

        if not channels:
            response_message = "❌ No channels configured for announcements."
        else:
            for channel in channels:
                ch_id_str = channel.get("chat_id")
                if not ch_id_str:
                    continue

                try:
                    ch_id = int(ch_id_str)
                    final_caption_html = f"📢 <b>Announcement:</b>\n\n{caption if caption else ''}"
                    final_text_html = f"📢 <b>Announcement:</b>\n\n{text_input if text_input else ''}"

                    if text_input:
                        await context.bot.send_message(chat_id=ch_id, text=final_text_html, parse_mode=ParseMode.HTML)
                        sent_to_any = True
                    elif photo:
                        await context.bot.send_photo(chat_id=ch_id, photo=photo[-1].file_id, caption=final_caption_html if caption else "📢 <b>Announcement</b>", parse_mode=ParseMode.HTML)
                        sent_to_any = True
                    elif video:
                        await context.bot.send_video(chat_id=ch_id, video=video.file_id, caption=final_caption_html if caption else "📢 <b>Announcement</b>", parse_mode=ParseMode.HTML)
                        sent_to_any = True
                    elif doc:
                        await context.bot.send_document(chat_id=ch_id, document=doc.file_id, caption=final_caption_html if caption else "📢 <b>Announcement</b>", parse_mode=ParseMode.HTML)
                        sent_to_any = True

                except ValueError:
                    logger.error(f"Invalid Channel ID: {ch_id_str}")
                except Exception as e_announce:
                    logger.error(f"Failed to send announcement to {ch_id_str}: {e_announce}")

            if sent_to_any:
                response_message = "✅ Announcement posted to configured channel(s)."
            elif not response_message:
                response_message = "⚠️ Announcement content not recognized or channels not configured."

    elif state == 'broadcast_to_users':
        users_data = load_json(USERS_JSON_FILE)
        sent_count = 0
        failed_count = 0

        for uid in users_data.keys():
            try:
                if text_input:
                    await context.bot.send_message(chat_id=uid, text=text_input, parse_mode=ParseMode.HTML)
                elif photo:
                    await context.bot.send_photo(chat_id=uid, photo=photo[-1].file_id, caption=caption if caption else None, parse_mode=ParseMode.HTML)
                elif video:
                    await context.bot.send_video(chat_id=uid, video=video.file_id, caption=caption if caption else None, parse_mode=ParseMode.HTML)
                elif doc:
                    await context.bot.send_document(chat_id=uid, document=doc.file_id, caption=caption if caption else None, parse_mode=ParseMode.HTML)

                sent_count += 1
            except Exception as e_broadcast:
                logger.error(f"Failed to broadcast to user {uid}: {e_broadcast}")
                failed_count += 1

        response_message = f"✅ Broadcast sent to {sent_count} users. Failed for {failed_count} users."
        response_parse_mode = ParseMode.HTML

    elif state == 'set_service_price':
        if not text_input:
            response_message = "No parameters provided."
        else:
            try:
                parts = text_input.split()
                if len(parts) != 2:
                    raise ValueError("Expected format: service_key new_price")

                s_key, s_price_str = parts[0], parts[1]
                s_price = int(s_price_str)

                if s_key not in services:
                    response_message = f"Service key '{s_key}' not found."
                elif update_service_price(s_key, s_price):
                    response_message = f"✅ Price for '{s_key}' updated to {s_price} points."
                else:
                    response_message = f"❌ Failed to update price for '{s_key}'."

            except ValueError:
                response_message = "Invalid format. Use `service_key new_price` (e.g., crunchyroll 150)."
            except Exception as e_sprice:
                logger.error(f"Error setting service price: {e_sprice}")
                response_message = "An error occurred."

    elif state == 'adjust_user_points':
        if not text_input:
            response_message = "No parameters provided."
        else:
            try:
                parts = text_input.split()
                if len(parts) != 2:
                    raise ValueError("Expected format: username_or_id points_change")

                target_user_input, points_str = parts[0], parts[1]
                points_change = int(points_str)

                user_id_to_update = None
                username_to_update = None

                if target_user_input.isdigit():
                    user_id_to_update = target_user_input
                    users_data = load_json(USERS_JSON_FILE)
                    user_data = users_data.get(user_id_to_update, {})
                    username_to_update = user_data.get('username')
                    if not username_to_update:
                        username_to_update = f"user{user_id_to_update}"
                else:
                    username_to_update = target_user_input.lstrip('@')
                    users_data = load_json(USERS_JSON_FILE)
                    for uid, data in users_data.items():
                        if data.get('username') == username_to_update:
                            user_id_to_update = uid
                            break

                if user_id_to_update:
                    if update_user_points(user_id_to_update, username_to_update, points_change):
                        new_total = get_user_points(user_id_to_update)
                        response_message = f"✅ Points for {username_to_update} (ID: {user_id_to_update}) adjusted by {points_change}. New total: {new_total}."
                    else:
                        response_message = f"❌ Failed to update points for {username_to_update}."
                else:
                    response_message = f"❌ User '{target_user_input}' not found."

            except ValueError:
                response_message = "Invalid format. Use `username_or_id points_change` (e.g., testuser 100 or 12345 -50)."
            except Exception as e_adjpts:
                logger.error(f"Error adjusting points: {e_adjpts}")
                response_message = "An error occurred."

    elif state == 'view_user_points':
        if not text_input:
            response_message = "No username or ID provided."
        else:
            target_user_input = text_input.strip()
            user_id_to_view = None
            username_to_view = None

            if target_user_input.isdigit():
                user_id_to_view = target_user_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_view, {})
                username_to_view = user_data.get('username')
                if not username_to_view:
                    username_to_view = f"user{user_id_to_view}"
            else:
                username_to_view = target_user_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == username_to_view:
                        user_id_to_view = uid
                        break

            if user_id_to_view:
                points = get_user_points(user_id_to_view)
                response_message = f"💰 Points for {username_to_view} (ID: {user_id_to_view}): {points}."
            else:
                response_message = f"❌ User '{target_user_input}' not found."

    elif state == 'add_points_all_users':
        if not text_input:
            response_message = "No points value provided."
        else:
            try:
                points_to_add = int(text_input.strip())
                if points_to_add == 0:
                    response_message = "Points value cannot be zero."
                else:
                    users_data = load_json(USERS_JSON_FILE)
                    updated_count = 0

                    for user_id in users_data.keys():
                        if update_user_points(user_id, None, points_to_add):
                            updated_count += 1

                    response_message = f"✅ Added {points_to_add} points to {updated_count} users."

            except ValueError:
                response_message = "Invalid points value. Please enter a number."
            except Exception as e:
                logger.error(f"Error adding points to all users: {e}")
                response_message = "An error occurred."

    elif state == 'set_points_all_users':
        if not text_input:
            response_message = "No points value provided."
        else:
            try:
                points_value = int(text_input.strip())
                if points_value < 0:
                    response_message = "Points value cannot be negative."
                else:
                    updated_count = set_points_for_all_users(points_value)
                    response_message = f"✅ Set points to {points_value} for {updated_count} users."

            except ValueError:
                response_message = "Invalid points value. Please enter a number."
            except Exception as e:
                logger.error(f"Error setting points for all users: {e}")
                response_message = "An error occurred."

    elif state == 'permanent_ban_user':
        if not text_input:
            response_message = "No user ID provided."
        else:
            user_id_to_ban = text_input.strip()
            if permanent_ban_user(user_id_to_ban):
                response_message = f"✅ User {user_id_to_ban} permanently banned."
            else:
                response_message = f"❌ Failed to permanently ban user {user_id_to_ban}."

    elif state == 'permanent_unban_user':
        if not text_input:
            response_message = "No user ID provided."
        else:
            user_id_to_unban = text_input.strip()
            if remove_permanent_ban(user_id_to_unban):
                response_message = f"✅ Permanent ban removed for user {user_id_to_unban}."
            else:
                response_message = f"❌ User {user_id_to_unban} is not permanently banned."

    elif state == 'search_user':
        if not text_input:
            response_message = "No username or ID provided."
        else:
            user_id_to_view = None
            username_to_view = None

            if text_input.isdigit():
                user_id_to_view = text_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_view, {})
                username_to_view = user_data.get('username')
                if not username_to_view:
                    username_to_view = f"user{user_id_to_view}"
            else:
                username_to_view = text_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == username_to_view:
                        user_id_to_view = uid
                        break

            if user_id_to_view:
                points = get_user_points(user_id_to_view)
                is_premium, premium_until = check_user_premium_status(user_id_to_view)
                referral_count = get_referral_count(user_id_to_view)
                claims = get_user_claims(user_id_to_view, limit=5)

                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_view, {})
                total_claims = user_data.get('total_claims', 0)
                join_date = user_data.get('join_date', 'Unknown')

                claims_text = "\n".join([f"  • {claim.get('service_key')} on {claim.get('timestamp', '')[:10]}" for claim in claims]) if claims else "  No claims yet"

                # Get transfer history
                transfer_history = get_transfer_history_text(user_id_to_view, limit=3)

                # Get redemption history
                redemption_history = get_redemption_history_text(user_id_to_view, limit=3)

                # Format username for display to avoid '@' prefix on fallback usernames
                username_display = format_username_display(username_to_view)
                response_message = (
                    f"📊 <b>User Statistics</b>\n\n"
                    f"👤 <b>Username:</b> {username_display}\n"
                    f"🆔 <b>ID:</b> <code>{user_id_to_view}</code>\n"
                    f"📅 <b>Joined:</b> {join_date}\n"
                    f"💰 <b>Points:</b> {points}\n"
                    f"💎 <b>Premium Status:</b> {'✅ Yes' if is_premium else '❌ No'}\n"
                    f"📅 <b>Premium Until:</b> {premium_until if premium_until else 'N/A'}\n"
                    f"👥 <b>Referrals:</b> {referral_count}\n"
                    f"📦 <b>Total Claims:</b> {total_claims}\n\n"
                    f"📋 <b>Recent Claims:</b>\n{claims_text}\n\n"
                    f"🔄 <b>Transfer History:</b>\n{transfer_history}\n\n"
                    f"🔑 <b>Redemption History:</b>\n{redemption_history}"
                )
            else:
                response_message = f"❌ User '{text_input}' not found."

    elif state == 'view_specific_user_claims':
        if not text_input:
            response_message = "No username or ID provided."
        else:
            user_id_to_view = None
            username_to_view = None

            if text_input.isdigit():
                user_id_to_view = text_input
                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_view, {})
                username_to_view = user_data.get('username')
                if not username_to_view:
                    username_to_view = f"user{user_id_to_view}"
            else:
                username_to_view = text_input.lstrip('@')
                users_data = load_json(USERS_JSON_FILE)
                for uid, data in users_data.items():
                    if data.get('username') == username_to_view:
                        user_id_to_view = uid
                        break

            if user_id_to_view:
                # Get user claims
                claims = get_user_claims(user_id_to_view, limit=50)

                users_data = load_json(USERS_JSON_FILE)
                user_data = users_data.get(user_id_to_view, {})
                total_claims = user_data.get('total_claims', 0)
                join_date = user_data.get('join_date', 'Unknown')
                points = get_user_points(user_id_to_view)

                username_display = format_username_display(username_to_view)

                response_message = (
                    f"📦 <b>Claim History for {username_display}</b>\n\n"
                    f"🆔 <b>ID:</b> <code>{user_id_to_view}</code>\n"
                    f"📊 <b>Total Claims:</b> {total_claims}\n"
                    f"💰 <b>Current Points:</b> {points}\n"
                    f"📅 <b>Joined:</b> {join_date}\n\n"
                )

                if claims:
                    response_message += "<b>📋 Recent Claims:</b>\n"
                    for i, claim in enumerate(claims[:20], 1):
                        service = claim.get('service_key', 'Unknown')
                        timestamp = claim.get('timestamp', 'Unknown')[:16]
                        response_message += f"{i}. {service} - {timestamp}\n"

                    if len(claims) > 20:
                        response_message += f"\n<i>... and {len(claims) - 20} more claims</i>"
                else:
                    response_message += "<i>No claims found for this user.</i>"
            else:
                response_message = f"❌ User '{text_input}' not found."

    if response_message:
        await safe_send_message(user.id, response_message, context, parse_mode=response_parse_mode)

    mock_update_for_menu = type('Update', (), {
        'callback_query': None,
        'message': update.message,
        'effective_user': user,
        'effective_chat': update.effective_chat
    })()
    mock_update_for_menu.callback_query = type('CallbackQuery', (), {
        'data': return_to_menu_cb_data,
        'message': None,
        'id': f'mock_query_after_input_nav_{state}_{time.time()}',
        'answer': lambda: asyncio.sleep(0)
    })()

    try:
        if return_to_menu_cb_data == 'admin_panel_main':
            await admin_panel_main_menu(mock_update_for_menu, context, edit_message=False)
        else:
            await admin_callback_router(mock_update_for_menu, context)
    except Exception as e_nav_back:
        logger.error(f"Error navigating back to admin menu '{return_to_menu_cb_data}' after state '{state}': {e_nav_back}")
        await admin_panel_main_menu(mock_update_for_menu, context, edit_message=False)

    context.user_data.pop('config_key_to_set', None)
    context.user_data.pop('config_description', None)
    context.user_data.pop('service_for_accounts', None)
    # ADD THESE LINES AT VERY END OF handle_admin_message_input FUNCTION
    context.user_data.pop('target_user_id', None)
    context.user_data.pop('target_username', None)
    context.user_data.pop('config_key_to_set', None)
    context.user_data.pop('config_description', None)
    context.user_data.pop('service_for_accounts', None)

# ------------------ DATA EXPORT/IMPORT COMMANDS ------------------
async def data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await safe_send_message(update.effective_chat.id, "You are not authorized to use this command.", context, parse_mode=ParseMode.HTML)
        return

    users_data = load_json(USERS_JSON_FILE)

    users_json = json.dumps(users_data, indent=2, ensure_ascii=False)
    users_file = io.BytesIO(users_json.encode('utf-8'))
    users_file.name = "users.json"

    await safe_send_document(update.effective_chat.id, users_file, context,
                            filename='users_data.json', caption="Bot users data",
                            parse_mode=ParseMode.HTML)

async def data_full_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ Access denied. Admin only.")
        return

    progress_message = await update.message.reply_text(
        "🔄 Starting data export...\n\nThis may take a few moments.",
        parse_mode=ParseMode.HTML
    )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            zip_filename = f"bot_backup_{timestamp}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)

            files_to_export = [
                ('config.json', CONFIG_FILE),
                ('admins.json', ADMINS_FILE),
                ('banned.json', BANNED_FILE),
                ('permanent_bans.json', PERMANENT_BANS_FILE),
                ('bot_status.json', BOT_STATUS_FILE),
                ('users.json', USERS_JSON_FILE),
                ('user_points.csv', USER_POINTS_FILE),
                ('services.json', SERVICES_FILE),
                ('service_prices.json', SERVICE_PRICES_FILE),
                ('service_items.json', SERVICE_ITEMS_FILE),
                ('keys.json', KEYS_FILE),
                ('point_keys.json', POINT_KEYS_FILE),
                ('referrals.csv', REFERRALS_FILE),
                ('feedbacks.json', FEEDBACKS_FILE),
                ('transfers.json', TRANSFERS_FILE),
                ('claimed_accounts.json', CLAIMED_ACCOUNTS_FILE)
            ]

            await progress_message.edit_text(
                "🔄 Collecting data from files...",
                parse_mode=ParseMode.HTML
            )

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                files_added = 0

                for filename, filepath in files_to_export:
                    if filepath.exists():
                        try:
                            if filename.endswith('.json'):
                                data = load_json(filepath)
                                temp_file = os.path.join(temp_dir, filename)
                                with open(temp_file, 'w', encoding='utf-8') as f:
                                    json.dump(data, f, indent=2, ensure_ascii=False)
                                zipf.write(temp_file, filename)
                            else:
                                zipf.write(filepath, filename)

                            files_added += 1

                            if files_added % 3 == 0:
                                await progress_message.edit_text(
                                    f"🔄 Collecting data... {files_added}/{len(files_to_export)} files",
                                    parse_mode=ParseMode.HTML
                                )

                            logger.info(f"Exported file: {filename}")

                        except Exception as e:
                            logger.error(f"Error exporting {filename}: {e}")

                metadata = {
                    "export_info": {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "exported_by": user.full_name,
                        "user_id": user.id,
                        "username": user.username,
                        "files_exported": files_added,
                        "bot_version": "1.0"
                    }
                }

                metadata_filename = "metadata.json"
                metadata_filepath = os.path.join(temp_dir, metadata_filename)
                with open(metadata_filepath, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                zipf.write(metadata_filepath, metadata_filename)
                files_added += 1

            file_size = os.path.getsize(zip_path)
            size_mb = file_size / (1024 * 1024)

            await progress_message.edit_text(
                f"📤 Sending backup file...\n\nFile size: {size_mb:.1f} MB\nFiles: {files_added}",
                parse_mode=ParseMode.HTML
            )

            try:
                with open(zip_path, 'rb') as zip_file:
                    await asyncio.wait_for(
                        context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=zip_file,
                            filename=zip_filename,
                            caption=(
                                f"<b>📦 Bot Data Export</b>\n\n"
                                f"<b>⏰ Timestamp:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                                f"<b>👤 Exported by:</b> <a href=\"tg://user?id={user.id}\">{html.escape(user.full_name)}</a>\n"
                                f"<b>📱 Username:</b> {format_username_display(user.username) if user.username else 'NoUsername'}\n"
                                f"<b>🆔 User ID:</b> <code>{user.id}</code>\n"
                                f"<b>📊 Files:</b> <code>{files_added}</code>\n"
                                f"<b>📁 File Size:</b> <code>{size_mb:.1f} MB</code>\n\n"
                                f"<i>Use /upload_data to restore this backup.</i>"
                            ),
                            parse_mode=ParseMode.HTML
                        ),
                        timeout=120.0
                    )

                await progress_message.delete()
                logger.info(f"Data export completed by admin {user.id}, {files_added} files, size: {size_mb:.1f} MB")

            except asyncio.TimeoutError:
                await progress_message.edit_text(
                    "❌ Failed to send backup file: Upload timed out.",
                    parse_mode=ParseMode.HTML
                )

    except Exception as e:
        logger.error(f"Error in data_full_command: {e}")
        try:
            await progress_message.edit_text(
                f"❌ Error exporting data: {str(e)}",
                parse_mode=ParseMode.HTML
            )
        except:
            await update.message.reply_text(
                f"❌ Error exporting data: {str(e)}",
                parse_mode=ParseMode.HTML
            )


async def debug_zip_structure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug function to see ZIP file structure"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if not update.message.document:
        await update.message.reply_text("Please upload a ZIP file to debug.")
        return

    document = update.message.document
    if not document.file_name.endswith('.zip'):
        await update.message.reply_text("Please upload a ZIP file.")
        return

    try:
        file = await context.bot.get_file(document.file_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, document.file_name)
            await file.download_to_drive(custom_path=zip_path)

            structure = []
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                for file_info in zipf.infolist():
                    structure.append(f"{file_info.filename} ({file_info.file_size} bytes)")

            if structure:
                message = "📁 <b>ZIP File Structure:</b>\n\n" + "\n".join(structure)
                if len(message) > 4000:
                    # Split into multiple messages
                    parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
                    for part in parts:
                        await update.message.reply_text(part, parse_mode=ParseMode.HTML)
                else:
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text("Empty ZIP file.")

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def process_zip_file(update: Update, context: ContextTypes.DEFAULT_TYPE, document):
    """Process uploaded ZIP file for data restoration"""
    user = update.effective_user
    progress_message = await update.message.reply_text(
        "📥 Downloading backup file...",
        parse_mode=ParseMode.HTML
    )

    try:
        file = await context.bot.get_file(document.file_id)

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, document.file_name)

        try:
            await progress_message.edit_text("📥 Downloading file...")
            await file.download_to_drive(custom_path=zip_path)

            await progress_message.edit_text("📦 Extracting archive...")
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(extract_dir)

            # Debug: Log directory structure
            logger.info(f"=== DEBUG: Directory structure of {extract_dir} ===")
            for root, dirs, files in os.walk(extract_dir):
                level = root.replace(extract_dir, '').count(os.sep)
                indent = ' ' * 4 * level
                logger.info(f'{indent}{os.path.basename(root)}/')
                subindent = ' ' * 4 * (level + 1)
                for file in files:
                    logger.info(f'{subindent}{file}')
            logger.info(f"=== END DEBUG ===")

            # Define supported files and their destinations
            supported_files = {
                'config.json': CONFIG_FILE,
                'admins.json': ADMINS_FILE,
                'banned.json': BANNED_FILE,
                'permanent_bans.json': PERMANENT_BANS_FILE,
                'bot_status.json': BOT_STATUS_FILE,
                'users.json': USERS_JSON_FILE,
                'user_points.csv': USER_POINTS_FILE,
                'services.json': SERVICES_FILE,
                'service_prices.json': SERVICE_PRICES_FILE,
                'service_items.json': SERVICE_ITEMS_FILE,
                'keys.json': KEYS_FILE,
                'point_keys.json': POINT_KEYS_FILE,
                'referrals.csv': REFERRALS_FILE,
                'feedbacks.json': FEEDBACKS_FILE,
                'transfers.json': TRANSFERS_FILE,
                'claimed_accounts.json': CLAIMED_ACCOUNTS_FILE
            }

            # Search for files in the extracted directory
            files_found = []
            file_paths = {}

            # Method 1: Check if files are directly in extract_dir
            direct_files = os.listdir(extract_dir)
            logger.info(f"Files in extract_dir root: {direct_files}")

            for filename in supported_files.keys():
                direct_path = os.path.join(extract_dir, filename)
                if os.path.exists(direct_path):
                    files_found.append(filename)
                    file_paths[filename] = direct_path
                    logger.info(f"Found file (direct): {filename} at {direct_path}")

            # Method 2: If no files found directly, search recursively
            if not files_found:
                logger.info("No files found directly, searching recursively...")
                for root, dirs, files in os.walk(extract_dir):
                    for filename in files:
                        if filename in supported_files and filename not in file_paths:
                            full_path = os.path.join(root, filename)
                            files_found.append(filename)
                            file_paths[filename] = full_path
                            logger.info(f"Found file (recursive): {filename} at {full_path}")

            # Method 3: Check for common backup structures
            if not files_found:
                # Check for timestamped folders (like bot_backup_2026-01-29_13-56-06)
                for item in os.listdir(extract_dir):
                    item_path = os.path.join(extract_dir, item)
                    if os.path.isdir(item_path) and item.startswith('bot_backup_'):
                        logger.info(f"Found backup folder: {item}")
                        for filename in supported_files.keys():
                            backup_file_path = os.path.join(item_path, filename)
                            if os.path.exists(backup_file_path):
                                files_found.append(filename)
                                file_paths[filename] = backup_file_path
                                logger.info(f"Found file in backup folder: {filename} at {backup_file_path}")

            if not files_found:
                # Clean up temp directory
                shutil.rmtree(temp_dir, ignore_errors=True)

                await progress_message.edit_text(
                    "❌ No valid bot data files found in the ZIP.\n\n"
                    "Please make sure you're uploading a backup file created by the /data_full command.\n\n"
                    f"Files found in ZIP: {', '.join(os.listdir(extract_dir)) if os.path.exists(extract_dir) else 'None'}",
                    parse_mode=ParseMode.HTML
                )
                return

            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, Restore Data", callback_data="confirm_restore"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_restore")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            context.user_data['pending_restore'] = {
                'temp_dir': temp_dir,
                'extract_dir': extract_dir,
                'supported_files': supported_files,
                'files_found': files_found,
                'file_paths': file_paths,  # Store actual paths
                'user_id': user.id,
                'progress_message_id': progress_message.message_id,
                'chat_id': update.effective_chat.id
            }

            file_list = "\n".join([f"• {f}" for f in files_found])

            await progress_message.edit_text(
                f"⚠️ <b>Data Restore Confirmation</b>\n\n"
                f"📁 Files to restore: {len(files_found)}/{len(supported_files)}\n\n"
                f"<b>Files found:</b>\n{file_list}\n\n"
                f"<b>WARNING:</b> This will OVERWRITE existing data!\n\n"
                f"Are you sure you want to continue?",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            # Clean up temp directory on error
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise e

    except zipfile.BadZipFile:
        await progress_message.edit_text("❌ Invalid ZIP file.")
    except Exception as e:
        logger.error(f"Error in process_zip_file: {e}", exc_info=True)
        await progress_message.edit_text(
            f"❌ Error processing file: {str(e)}",
            parse_mode=ParseMode.HTML
        )

async def upload_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ Access denied. Admin only.")
        return

    # Check if message contains a document
    if update.message.document:
        document = update.message.document
        if not document.file_name or not document.file_name.endswith('.zip'):
            await update.message.reply_text("❌ Please upload a ZIP file.")
            return

        # Process the ZIP file directly
        await process_zip_file(update, context, document)
    else:
        # If no document, set up state to wait for it
        context.user_data['awaiting_admin_input'] = 'upload_data'
        await update.message.reply_text(
            "📤 Please upload a ZIP file containing bot data.\n\n"
            "This should be a backup file created by the /data_full command.\n\n"
            "Send the ZIP file now, or type /cancel_admin to cancel.",
            parse_mode=ParseMode.HTML
        )

async def handle_restore_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.edit_message_text("❌ Access denied.")
        return

    pending_restore = context.user_data.get('pending_restore')
    if not pending_restore or pending_restore['user_id'] != user.id:
        await query.edit_message_text("❌ Restore session expired.")
        return

    if query.data == 'cancel_restore':
        await query.edit_message_text("❌ Data restore cancelled.", parse_mode=ParseMode.HTML)
        # Clean up temp directory
        temp_dir = pending_restore.get('temp_dir')
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up temp directory on cancel: {temp_dir}")
        context.user_data.pop('pending_restore', None)
        return

    if query.data == 'confirm_restore':
        temp_dir = None
        try:
            await query.edit_message_text("🔄 Restoring data...", parse_mode=ParseMode.HTML)

            temp_dir = pending_restore['temp_dir']
            extract_dir = pending_restore['extract_dir']
            supported_files = pending_restore['supported_files']
            files_found = pending_restore['files_found']
            file_paths = pending_restore['file_paths']  # Get the actual file paths

            restored_files = []
            errors = []

            for filename in files_found:
                try:
                    # Get the actual path from file_paths
                    source_path = file_paths.get(filename)
                    if not source_path:
                        errors.append(f"{filename}: File path not found in stored paths")
                        logger.error(f"File path for {filename} not found in file_paths dict")
                        continue

                    # Check if source file actually exists
                    if not os.path.exists(source_path):
                        errors.append(f"{filename}: Source file not found at {source_path}")
                        logger.error(f"Source file doesn't exist: {source_path}")

                        # Try to find it again
                        logger.info(f"Trying to locate {filename} in extract_dir...")
                        found_alternative = False
                        for root, dirs, files in os.walk(extract_dir):
                            if filename in files:
                                alternative_path = os.path.join(root, filename)
                                if os.path.exists(alternative_path):
                                    source_path = alternative_path
                                    found_alternative = True
                                    logger.info(f"Found alternative path: {source_path}")
                                    break

                        if not found_alternative:
                            continue

                    dest_path = supported_files[filename]

                    # Create destination directory if needed
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                    logger.info(f"Restoring {filename} from {source_path} to {dest_path}")

                    if filename.endswith('.json'):
                        # Load JSON to validate it's valid JSON
                        with open(source_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        save_json(dest_path, data)
                    elif filename.endswith('.csv'):
                        # For CSV files, copy directly
                        shutil.copy2(source_path, dest_path)
                    else:
                        # For other files, copy directly
                        shutil.copy2(source_path, dest_path)

                    restored_files.append(filename)
                    logger.info(f"Successfully restored: {filename}")

                except json.JSONDecodeError as e:
                    error_msg = f"{filename}: Invalid JSON format - {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"JSON error restoring {filename}: {e}")
                except Exception as e:
                    error_msg = f"{filename}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"Error restoring {filename}: {e}", exc_info=True)

            # Reload data after restore
            if 'config.json' in restored_files:
                load_config()

            if 'services.json' in restored_files:
                load_services()

            if 'admins.json' in restored_files:
                load_admins()

            if 'banned.json' in restored_files or 'permanent_bans.json' in restored_files:
                load_banned_users()

            if 'bot_status.json' in restored_files:
                global bot_enabled
                bot_status_data = load_json(BOT_STATUS_FILE)
                bot_enabled = bot_status_data.get("enabled", True)

            # Clean up temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temp directory: {temp_dir}")

            # Prepare success message
            message = f"✅ <b>Data Restore Completed</b>\n\n"
            message += f"📊 Restored {len(restored_files)}/{len(files_found)} files\n\n"

            if restored_files:
                message += f"<b>✅ Restored files:</b>\n"
                for f in restored_files[:10]:  # Show only first 10 to avoid message too long
                    message += f"• {f}\n"
                if len(restored_files) > 10:
                    message += f"• ... and {len(restored_files) - 10} more\n"
                message += "\n"

            if errors:
                message += f"<b>❌ Errors ({len(errors)}):</b>\n"
                for error in errors[:5]:  # Show only first 5 errors
                    message += f"• {error}\n"
                if len(errors) > 5:
                    message += f"• ... and {len(errors) - 5} more errors\n"
                message += "\n"

            message += f"👤 <b>Restored by:</b> {html.escape(user.full_name)}\n"
            if user.username:
                message += f"📱 <b>Username:</b> {format_username_display(user.username)}\n"
            message += f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            message += f"⏰ <b>Timestamp:</b> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

            await query.edit_message_text(message, parse_mode=ParseMode.HTML)

            # Clear pending restore
            context.user_data.pop('pending_restore', None)

        except Exception as e:
            logger.error(f"Error in restore process: {e}", exc_info=True)

            # Clean up temp directory on error
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

            await query.edit_message_text(
                f"❌ <b>Restore Failed</b>\n\nError: {str(e)}",
                parse_mode=ParseMode.HTML
            )
            context.user_data.pop('pending_restore', None)


# ------------------ MAIN ------------------
def main() -> None:
    try:
        initialize_bot_systems()
        logger.info("Bot starting...")

        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("referral", referral))
        application.add_handler(CommandHandler("transfer", transfer_points))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("cancel_admin", cancel_admin_input))
        application.add_handler(CommandHandler("data", data_command))
        application.add_handler(CommandHandler("cancel", cancel_feedback))
        application.add_handler(CommandHandler("data_full", data_full_command))
        application.add_handler(CommandHandler("upload_data", upload_data_command))
        application.add_handler(CommandHandler("checkstock", check_stock_command))
        application.add_handler(CommandHandler("debugzip", debug_zip_structure))



        feedback_conversation_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(start_feedback, pattern="^send_feedback$"),
                CallbackQueryHandler(start_feedback, pattern="^feedback_[a-zA-Z0-9_]+$")
            ],
            states={
                WAITING_FOR_FEEDBACK: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, send_feedback_message),
                    CommandHandler("cancel", cancel_feedback)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_feedback)]
        )
        application.add_handler(feedback_conversation_handler)

        feedback_reply_conversation_handler = ConversationHandler(
            entry_points=[],
            states={
                AWAITING_ADMIN_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback_reply),
                    CommandHandler("cancel", cancel_feedback_reply)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_feedback_reply)]
        )
        application.add_handler(feedback_reply_conversation_handler)

        transfer_conversation_handler = ConversationHandler(
            entry_points=[
                CommandHandler("transfer", start_transfer),
                CallbackQueryHandler(start_transfer, pattern="^transfer_points$")
            ],
            states={
                1: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_recipient)],
                2: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)]
            },
            fallbacks=[CommandHandler("cancel", cancel_transfer)]
        )
        application.add_handler(transfer_conversation_handler)

        search_conversation_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(search_user_stats, pattern="^search_stats$"),
                CallbackQueryHandler(admin_search_user_menu, pattern="^admin_search_user$")
            ],
            states={
                SEARCH_USER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_input),
                    CommandHandler("cancel", cancel_search)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_search)]
        )
        application.add_handler(search_conversation_handler)

        application.add_handler(CallbackQueryHandler(handle_restore_confirmation, pattern="^(confirm_restore|cancel_restore)$"))

        application.add_handler(CallbackQueryHandler(handle_feedback_navigation, pattern="^fb_nav_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_actions, pattern="^fb_reply_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_actions, pattern="^fb_mark_read_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_actions, pattern="^fb_mark_new_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_actions, pattern="^fb_delete_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_delete_confirmation, pattern="^fb_confirm_delete_"))
        application.add_handler(CallbackQueryHandler(handle_feedback_delete_confirmation, pattern="^fb_cancel_delete$"))

        redeem_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("redeem", redeem_key_command)],
            states={WAITING_FOR_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_key_input)]},
            fallbacks=[CommandHandler("cancel", cancel_key_redemption)])
        application.add_handler(redeem_conv_handler)

        redeem_point_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("redeem_point", redeem_point_command)],
            states={WAITING_FOR_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_point_key_input)]},
            fallbacks=[CommandHandler("cancel", cancel_key_redemption)])
        application.add_handler(redeem_point_conv_handler)

        application.add_handler(CallbackQueryHandler(handle_button_click))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message_input))
        application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_admin_message_input))

        logger.info("Bot polling started.")
        application.run_polling()

    except Exception as e:
        logger.critical(f"Critical error in main: {e}", exc_info=True)

if __name__ == '__main__':
    main()

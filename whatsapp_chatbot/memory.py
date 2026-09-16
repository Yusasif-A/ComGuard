"""
Conversation checkpointing.

LangGraph stores each thread's message history in MongoDB, keyed by the thread id
(the reporter's number). That is what lets somebody send a photo, then a voice
note two minutes later, and have the assistant still know what they are talking
about.

Returning None when the database is unreachable is deliberate: the agent runs
without a checkpointer, losing memory between messages but still answering. For a
safety line, a forgetful assistant beats no assistant.
"""

import logging
import os

from dotenv import load_dotenv
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient
from pymongo.server_api import ServerApi

load_dotenv()

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB_NAME", "comguard")
COLLECTION_NAME = os.getenv("MONGO_CHECKPOINT_COLLECTION", "conversation_memory")


class SafeMongoDBSaver(MongoDBSaver):
    """MongoDBSaver that treats an unreadable checkpoint as a missing one.

    LangGraph has changed its serialised checkpoint format across versions, and
    loading an old one raises rather than returning nothing. Crashing on it would
    take out the thread permanently — every message that person ever sends would
    fail on the same stored row. Starting a fresh session instead costs them the
    context of one conversation, and the next checkpoint is written in the
    current format.
    """

    def get_tuple(self, config):
        try:
            return super().get_tuple(config)
        except Exception as e:
            message = str(e)
            if "too many values to unpack" in message or "loads_typed" in message:
                thread_id = config.get("configurable", {}).get("thread_id", "?")
                logger.warning(
                    f"⚠️ Incompatible checkpoint for thread '{thread_id}' — "
                    f"starting a fresh session. ({e})"
                )
                return None
            raise


def get_memory():
    """Build the checkpointer, or None if MongoDB is not reachable."""
    if not MONGO_URI:
        logger.warning("⚠️ MONGO_URI not set — conversations will not be remembered")
        return None

    try:
        client = MongoClient(MONGO_URI, server_api=ServerApi("1"))
        client.admin.command("ping")
        collection = client.get_database(DB_NAME).get_collection(COLLECTION_NAME)
        logger.info(f"✅ Conversation memory ready ({DB_NAME}.{COLLECTION_NAME})")
        return SafeMongoDBSaver(collection)
    except Exception as e:
        logger.error(f"❌ Could not connect to MongoDB for memory: {e}")
        return None

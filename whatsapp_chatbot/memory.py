from pymongo import MongoClient
from pymongo.server_api import ServerApi
from langgraph.checkpoint.mongodb import MongoDBSaver
import logging
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
logger = logging.getLogger(__name__)

class SafeMongoDBSaver(MongoDBSaver):
    """
    Wraps MongoDBSaver to handle checkpoint deserialization errors gracefully.

    Older LangGraph versions saved checkpoints with a 3-element tuple format.
    The current version expects 2-element tuples, so loading old data raises:
        ValueError: too many values to unpack (expected 2)

    Instead of crashing, we treat incompatible checkpoints as missing and let
    the agent start a fresh session. New checkpoints will be saved correctly.
    """

    def get_tuple(self, config):
        try:
            return super().get_tuple(config)
        except (ValueError, Exception) as e:
            if "too many values to unpack" in str(e) or "loads_typed" in str(e):
                thread_id = config.get("configurable", {}).get("thread_id", "?")
                logger.warning(
                    f"⚠️ Incompatible checkpoint for thread '{thread_id}' — starting fresh. "
                    f"(Old format: {e})"
                )
                return None
            raise

def get_memory():
    connection_string = MONGO_URI

    client = MongoClient(connection_string, server_api=ServerApi('1'))

    try:
        client.admin.command('ping')
        print("Connected to MongoDB Atlas!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return None

    db = client.get_database('Jeun-daada')
    collection = db.get_collection("jeun-daada-memory1")
    memory = SafeMongoDBSaver(collection)
    return memory

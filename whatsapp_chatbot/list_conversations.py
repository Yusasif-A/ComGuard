"""
Script to list all conversations in MongoDB to see the actual data structure
"""
import os
import logging
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def list_all_conversations():
    """List all conversations to see the actual data structure"""
    connection_string = os.getenv("MONGO_URI")
    if not connection_string:
        logger.error("MONGO_URI is not set in .env file")
        return
    
    try:
        # Connect to MongoDB
        client = MongoClient(connection_string, server_api=ServerApi('1'))
        client.admin.command('ping')
        logger.info("✅ Connected to MongoDB Atlas")
        
        # Get the database and collection
        db = client.get_database("Helpmum-Hackthon")
        conversations_collection = db.get_collection("conversations")
        
        # Get total count
        total_count = conversations_collection.count_documents({})
        print(f"\n{'='*60}")
        print(f"TOTAL CONVERSATIONS IN DATABASE: {total_count}")
        print(f"{'='*60}\n")
        
        if total_count == 0:
            print("⚠️  No conversations found in the database!")
            return
        
        # Get unique user_ids
        unique_users = conversations_collection.distinct("user_id")
        print(f"UNIQUE USERS: {len(unique_users)}")
        print(f"{'-'*60}")
        for user in unique_users[:20]:  # Show first 20 users
            count = conversations_collection.count_documents({"user_id": user})
            print(f"  {user}: {count} conversations")
        
        if len(unique_users) > 20:
            print(f"  ... and {len(unique_users) - 20} more users")
        
        print(f"\n{'-'*60}")
        print("SAMPLE CONVERSATIONS (first 5):")
        print(f"{'-'*60}\n")
        
        # Get first 5 conversations as samples
        sample_conversations = conversations_collection.find().limit(5)
        
        for i, conv in enumerate(sample_conversations, 1):
            print(f"Conversation #{i}:")
            print(f"  user_id: {conv.get('user_id')}")
            print(f"  user_name: {conv.get('user_name')}")
            print(f"  question: {conv.get('question', '')[:100]}...")
            print(f"  response: {conv.get('response', '')[:100]}...")
            print(f"  feedback: {conv.get('feedback')}")
            print(f"  timestamp: {conv.get('timestamp')}")
            print()
        
        # Close connection
        client.close()
        
    except Exception as e:
        logger.error(f"❌ Failed to list conversations: {e}")


if __name__ == "__main__":
    list_all_conversations()

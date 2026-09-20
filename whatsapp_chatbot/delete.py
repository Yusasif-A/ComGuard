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


def delete_user_conversations(user_id: str, delete_settings: bool = False):
    """
    Delete all conversations for a specific user from MongoDB
    
    Args:
        user_id: User's phone number (e.g., '08020812523')
        delete_settings: If True, also delete user settings (journey, language, terms_accepted)
                        This will trigger the terms/onboarding message again
    
    Returns:
        dict: Number of conversations and settings deleted
    """
    connection_string = os.getenv("MONGO_URI")
    if not connection_string:
        logger.error("MONGO_URI is not set in .env file")
        return {"conversations": 0, "settings": 0}
    
    try:
        # Connect to MongoDB
        client = MongoClient(connection_string, server_api=ServerApi('1'))
        client.admin.command('ping')
        logger.info("✅ Connected to MongoDB Atlas")
        
        # Get the database and collections
        db = client.get_database("comguard")
        conversations_collection = db.get_collection("conversations")
        settings_collection = db.get_collection("user_settings")
        
        # Count conversations before deletion
        count_before = conversations_collection.count_documents({"user_id": user_id})
        logger.info(f"Found {count_before} conversations for user {user_id}")
        
        # Delete conversations
        conv_deleted = 0
        if count_before > 0:
            result = conversations_collection.delete_many({"user_id": user_id})
            conv_deleted = result.deleted_count
            logger.info(f"✅ Deleted {conv_deleted} conversations for user {user_id}")
        else:
            logger.info(f"No conversations found for user {user_id}")
        
        # Delete settings if requested
        settings_deleted = 0
        if delete_settings:
            settings_result = settings_collection.delete_one({"user_id": user_id})
            settings_deleted = settings_result.deleted_count
            if settings_deleted > 0:
                logger.info(f"✅ Deleted user settings for {user_id} (will trigger terms/onboarding)")
            else:
                logger.info(f"No settings found for user {user_id}")
        
        # Close connection
        client.close()
        
        return {"conversations": conv_deleted, "settings": settings_deleted}
        
    except Exception as e:
        logger.error(f"❌ Failed to delete data: {e}")
        return {"conversations": 0, "settings": 0}


if __name__ == "__main__":
    phone_numbers = [
        "2348020812523",
        "2348134232353",
        # "2348142392322"
        # "23481"
    ]
    
    print("=" * 60)
    print("CONVERSATION DELETION SCRIPT")
    print("=" * 60)
    print("\nThis script will delete conversations for the following users:")
    for number in phone_numbers:
        print(f"  - {number}")
    
    # Ask about deleting settings
    print("\n⚠️  DELETE USER SETTINGS TOO?")
    print("   - If YES: User will see terms/onboarding message again (like a new user)")
    print("   - If NO:  Only conversations deleted, user keeps their journey/language")
    delete_settings = input("\nDelete settings too? (yes/no): ").lower() == "yes"
    
    # Ask for confirmation
    confirmation = input("\n⚠️  Are you sure you want to delete? (yes/no): ")
    
    if confirmation.lower() == "yes":
        print("\nStarting deletion...\n")
        total_conv_deleted = 0
        total_settings_deleted = 0
        
        for phone_number in phone_numbers:
            result = delete_user_conversations(phone_number, delete_settings=delete_settings)
            total_conv_deleted += result["conversations"]
            total_settings_deleted += result["settings"]
            print()
        
        print("=" * 60)
        print(f"TOTAL CONVERSATIONS DELETED: {total_conv_deleted}")
        print(f"TOTAL SETTINGS DELETED: {total_settings_deleted}")
        print("=" * 60)
    else:
        print("\n❌ Deletion cancelled.")

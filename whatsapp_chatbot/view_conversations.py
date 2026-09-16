"""
View all conversations from the MongoDB database to check model responses.
Shows conversation history with user messages and AI responses.
Can export to JSON for easier reading.
"""

import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")


def export_to_json():
    """Export all conversations to a JSON file."""
    client = MongoClient(MONGO_URI)
    db = client.get_database("Helpmum-Hackthon")
    conversations_collection = db["conversations"]
    settings_collection = db["user_settings"]
    
    # Get all conversations
    all_conversations = list(conversations_collection.find({}).sort("timestamp", -1))
    
    # Get unique users
    unique_users = conversations_collection.distinct("user_id")
    
    # Prepare export data
    export_data = {
        "export_date": datetime.utcnow().isoformat(),
        "total_users": len(unique_users),
        "total_conversations": len(all_conversations),
        "users": []
    }
    
    # Group conversations by user
    user_conversations = {}
    for conv in all_conversations:
        user_id = conv.get("user_id", "Unknown")
        if user_id not in user_conversations:
            user_conversations[user_id] = []
        user_conversations[user_id].append(conv)
    
    # Process each user
    for phone, convs in user_conversations.items():
        # Get user settings
        user_settings = settings_collection.find_one({"user_id": phone})
        journey = user_settings.get("journey", "Not set") if user_settings else "Not set"
        language = user_settings.get("language", "Not set") if user_settings else "Not set"
        baby_age = user_settings.get("baby_age_months", "Not set") if user_settings else "Not set"
        
        user_data = {
            "phone": phone,
            "journey": journey,
            "language": language,
            "baby_age_months": baby_age,
            "total_conversations": len(convs),
            "conversations": []
        }
        
        # Add conversations
        for conv in convs:
            # Convert datetime objects to strings
            timestamp = conv.get("timestamp", "")
            if isinstance(timestamp, datetime):
                timestamp = timestamp.isoformat()
            elif timestamp:
                try:
                    timestamp = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00')).isoformat()
                except:
                    timestamp = str(timestamp)
            
            conv_data = {
                "timestamp": timestamp,
                "user_name": conv.get("user_name", "Unknown"),
                "question": conv.get("question", ""),
                "response": conv.get("response", ""),
                "feedback": conv.get("feedback", None)
            }
            user_data["conversations"].append(conv_data)
        
        export_data["users"].append(user_data)
    
    # Save to JSON file
    filename = f"conversations_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Exported {len(all_conversations)} conversations from {len(unique_users)} users to {filename}")
    
    client.close()
    return filename

def view_all_conversations():
    """View all conversations from the database with stats."""
    client = MongoClient(MONGO_URI)
    db = client.get_database("Helpmum-Hackthon")
    conversations_collection = db["conversations"]
    settings_collection = db["user_settings"]
    
    # Get all conversations
    all_conversations = list(conversations_collection.find({}).sort("timestamp", -1))
    
    # Get unique users
    unique_users = conversations_collection.distinct("user_id")
    total_users = len(unique_users)
    total_messages = len(all_conversations)
    
    print("=" * 80)
    print(f"📊 CONVERSATION DATABASE STATISTICS")
    print("=" * 80)
    print(f"Total Users: {total_users}")
    print(f"Total Conversations: {total_messages}")
    print()
    
    # Group conversations by user
    user_conversations = {}
    for conv in all_conversations:
        user_id = conv.get("user_id", "Unknown")
        if user_id not in user_conversations:
            user_conversations[user_id] = []
        user_conversations[user_id].append(conv)
    
    # Process each user
    for idx, (phone, convs) in enumerate(user_conversations.items(), 1):
        # Get user settings
        user_settings = settings_collection.find_one({"user_id": phone})
        journey = user_settings.get("journey", "Not set") if user_settings else "Not set"
        language = user_settings.get("language", "Not set") if user_settings else "Not set"
        baby_age = user_settings.get("baby_age_months", "Not set") if user_settings else "Not set"
        
        message_count = len(convs)
        
        print(f"\n{'=' * 80}")
        print(f"USER #{idx}: {phone}")
        print(f"{'=' * 80}")
        print(f"Journey: {journey} | Language: {language} | Baby Age: {baby_age} months")
        print(f"Total Conversations: {message_count}")
        print("-" * 80)
        
        # Show last 5 conversations
        recent_convs = convs[:5]  # Already sorted by timestamp descending
        
        for conv in recent_convs:
            question = conv.get("question", "")
            response = conv.get("response", "")
            feedback = conv.get("feedback", "None")
            timestamp = conv.get("timestamp", "")
            user_name = conv.get("user_name", "Unknown")
            
            # Format timestamp
            if timestamp:
                try:
                    if isinstance(timestamp, str):
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    else:
                        dt = timestamp
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = str(timestamp)
            else:
                time_str = "No timestamp"
            
            # Display conversation
            print(f"\n📅 {time_str} | 👤 {user_name}")
            print(f"👤 USER:")
            print(f"   {question[:300]}")
            print(f"\n🤖 AI:")
            print(f"   {response[:300]}")
            if feedback and feedback != "None":
                feedback_emoji = "👍" if feedback == "like" else "👎"
                print(f"\n{feedback_emoji} Feedback: {feedback}")
            print("-" * 80)
        
        if message_count > 5:
            print(f"\n... ({message_count - 5} older conversations not shown)")
    
    print("\n" + "=" * 80)
    print(f"📊 SUMMARY")
    print("=" * 80)
    print(f"Total Users: {total_users}")
    print(f"Total Conversations: {total_messages}")
    print(f"Average Conversations per User: {total_messages / total_users if total_users > 0 else 0:.1f}")
    print("=" * 80)
    
    client.close()


def view_user_conversation(phone_number):
    """View conversation for a specific user."""
    client = MongoClient(MONGO_URI)
    db = client.get_database("Helpmum-Hackthon")
    conversations_collection = db["conversations"]
    settings_collection = db["user_settings"]
    
    # Get all conversations for this user
    convs = list(conversations_collection.find({"user_id": phone_number}).sort("timestamp", -1))
    
    if not convs:
        print(f"No conversations found for {phone_number}")
        client.close()
        return
    
    # Get user settings
    user_settings = settings_collection.find_one({"user_id": phone_number})
    journey = user_settings.get("journey", "Not set") if user_settings else "Not set"
    language = user_settings.get("language", "Not set") if user_settings else "Not set"
    baby_age = user_settings.get("baby_age_months", "Not set") if user_settings else "Not set"
    
    print("=" * 80)
    print(f"USER: {phone_number}")
    print("=" * 80)
    print(f"Journey: {journey} | Language: {language} | Baby Age: {baby_age} months")
    print(f"Total Conversations: {len(convs)}")
    print("-" * 80)
    
    for conv in convs:
        question = conv.get("question", "")
        response = conv.get("response", "")
        feedback = conv.get("feedback", "None")
        timestamp = conv.get("timestamp", "")
        user_name = conv.get("user_name", "Unknown")
        
        if timestamp:
            try:
                if isinstance(timestamp, str):
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                else:
                    dt = timestamp
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = str(timestamp)
        else:
            time_str = "No timestamp"
        
        print(f"\n📅 {time_str} | 👤 {user_name}")
        print(f"👤 USER:")
        print(f"{question}")
        print(f"\n🤖 AI:")
        print(f"{response}")
        if feedback and feedback != "None":
            feedback_emoji = "👍" if feedback == "like" else "👎"
            print(f"\n{feedback_emoji} Feedback: {feedback}")
        print("-" * 80)
    
    print("\n" + "=" * 80)
    
    client.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--export" or sys.argv[1] == "-e":
            # Export to JSON
            export_to_json()
        else:
            # View specific user
            phone = sys.argv[1]
            view_user_conversation(phone)
    else:
        # View all conversations
        view_all_conversations()

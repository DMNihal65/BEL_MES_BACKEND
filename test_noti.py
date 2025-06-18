#!/usr/bin/env python3
"""
Debug script to test the notification system and identify issues
"""

import sys
import os
from datetime import datetime

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pony.orm import db_session, commit
from app.database.connection import connect_to_db
from app.models.logs import (
    MachineStatusLog, RawMaterialStatusLog, MachineCalibrationLog,
    InstrumentCalibrationLog, PokaYokeCompletedLog, NotificationLog
)


def test_database_connection():
    """Test if database connection is working"""
    print("1. Testing database connection...")
    try:
        connect_to_db()
        print("✅ Database connection successful")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


def test_notification_log_table():
    """Test if NotificationLog table exists and can be accessed"""
    print("\n2. Testing NotificationLog table...")
    try:
        with db_session:
            # Try to query the table
            count = NotificationLog.select().count()
            print(f"✅ NotificationLog table accessible, current count: {count}")
            return True
    except Exception as e:
        print(f"❌ NotificationLog table error: {e}")
        return False


def test_machine_status_log_creation():
    """Test creating a MachineStatusLog and see if notification is triggered"""
    print("\n3. Testing MachineStatusLog creation...")
    try:
        with db_session:
            # Create a test machine status log
            machine_log = MachineStatusLog(
                machine_id=999,
                machine_make="Test Machine",
                status_name="Test Status",
                description="Test Description",
                updated_at=datetime.now(),
                created_by="test_user"
            )
            commit()

            print(f"✅ MachineStatusLog created with ID: {machine_log.id}")

            # Check if notification was created
            notifications = list(NotificationLog.select().order_by(lambda n: desc(n.created_at)).limit(1))
            if notifications:
                latest = notifications[0]
                print(f"✅ Notification created: ID={latest.id}, Title='{latest.title}'")
                return True
            else:
                print("❌ No notification was created")
                return False

    except Exception as e:
        print(f"❌ Error creating MachineStatusLog: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_raw_material_status_log_creation():
    """Test creating a RawMaterialStatusLog and see if notification is triggered"""
    print("\n4. Testing RawMaterialStatusLog creation...")
    try:
        with db_session:
            # Create a test raw material status log
            material_log = RawMaterialStatusLog(
                material_id=888,
                part_number="TEST-001",
                status_name="Test Material Status",
                description="Test Material Description",
                updated_at=datetime.now(),
                created_by="test_user"
            )
            commit()

            print(f"✅ RawMaterialStatusLog created with ID: {material_log.id}")

            # Check if notification was created
            notifications = list(NotificationLog.select().order_by(lambda n: desc(n.created_at)).limit(1))
            if notifications:
                latest = notifications[0]
                print(f"✅ Notification created: ID={latest.id}, Title='{latest.title}'")
                return True
            else:
                print("❌ No notification was created")
                return False

    except Exception as e:
        print(f"❌ Error creating RawMaterialStatusLog: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_direct_notification_creation():
    """Test creating a notification directly"""
    print("\n5. Testing direct notification creation...")
    try:
        with db_session:
            # Create a notification directly
            notification = NotificationLog(
                source_table="test_table",
                source_id=123,
                notification_type="test_type",
                title="Test Notification",
                message="This is a test notification"
            )
            commit()

            print(f"✅ Direct notification created with ID: {notification.id}")
            return True

    except Exception as e:
        print(f"❌ Error creating direct notification: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_after_insert_hook():
    """Test if after_insert hooks are being called"""
    print("\n6. Testing after_insert hook...")
    try:
        # Import the notification functions to test them directly
        from app.api.v1.endpoints.simple_notification_service import notify_machine_status

        with db_session:
            # Test the notification function directly
            result = notify_machine_status(
                machine_id=777,
                machine_make="Direct Test Machine",
                status_name="Direct Test Status",
                description="Direct Test Description"
            )

            if result:
                print(f"✅ Direct notification function worked: ID={result.id}")
                return True
            else:
                print("❌ Direct notification function failed")
                return False

    except Exception as e:
        print(f"❌ Error testing after_insert hook: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_all_notifications():
    """Check all notifications in the database"""
    print("\n7. Checking all notifications...")
    try:
        with db_session:
            notifications = list(NotificationLog.select().order_by(lambda n: desc(n.created_at)))
            print(f"Total notifications in database: {len(notifications)}")

            for i, notification in enumerate(notifications[:5]):  # Show last 5
                print(
                    f"  {i + 1}. ID={notification.id}, Type={notification.notification_type}, Title='{notification.title}', Created={notification.created_at}")

            return True

    except Exception as e:
        print(f"❌ Error checking notifications: {e}")
        return False


def main():
    print("🔍 Debugging Notification System")
    print("=" * 50)

    # Run all tests
    tests = [
        test_database_connection,
        test_notification_log_table,
        test_direct_notification_creation,
        test_after_insert_hook,
        test_machine_status_log_creation,
        test_raw_material_status_log_creation,
        check_all_notifications
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            results.append(False)

    # Summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY")
    print("=" * 50)

    passed = sum(results)
    total = len(results)

    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed! Notification system should be working.")
    else:
        print("⚠️  Some tests failed. Check the issues above.")

        if not results[0]:  # Database connection failed
            print("💡 Issue: Database connection problem")
        elif not results[1]:  # NotificationLog table failed
            print("💡 Issue: NotificationLog table not accessible")
        elif not results[2]:  # Direct notification failed
            print("💡 Issue: Cannot create notifications directly")
        elif not results[3]:  # After insert hook failed
            print("💡 Issue: Notification functions not working")
        elif not results[4] or not results[5]:  # Log creation failed
            print("💡 Issue: after_insert hooks not triggering")


if __name__ == "__main__":
    main()
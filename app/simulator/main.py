from pony.orm import db_session

from app.database.connection import connect_to_db


def startup_event():
    try:
        connect_to_db()
    except Exception as e:
        print(f"Error connecting to database: {str(e)}")
        raise e


if __name__ == '__main__':
    startup_event()

"""Run this once: python scripts/seed_data.py"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import create_tables, SessionLocal, Student, JobDescription
from services.rag_service import embed_jd
import hashlib, uuid

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

def seed():
    create_tables()
    db = SessionLocal()
    try:
        # Seed students
        if not db.query(Student).first():
            students = [
                Student(name="Rahul Sharma", email="rahul@college.edu",
                        role="student", cgpa=8.5, branch="Computer Science",
                        year_of_passing=2026,
                        skills=["Python","React","SQL","DSA","System Design"],
                        password_hash=hash_pw("password123")),
                Student(name="Priya Patel", email="priya@college.edu",
                        role="student", cgpa=9.1, branch="Computer Science",
                        year_of_passing=2026,
                        skills=["Java","Spring Boot","MySQL","AWS"],
                        password_hash=hash_pw("password123")),
                Student(name="Officer Singh", email="officer@college.edu",
                        role="officer", cgpa=0, branch="Admin",
                        year_of_passing=2020,
                        password_hash=hash_pw("officer123")),
            ]
            for s in students:
                db.add(s)
            db.commit()
            print("✅ Students seeded")

        # Seed JDs
        if not db.query(JobDescription).first():
            jds_data = [
                {
                    "company_name": "Google",
                    "role_title": "SDE Intern",
                    "required_skills": ["Python","DSA","System Design","React"],
                    "cgpa_cutoff": 7.5,
                    "jd_text": """Google SDE Intern - Summer 2026
                    Requirements: Strong knowledge of Data Structures and Algorithms.
                    Proficiency in Python or Java. Experience with web development using React or Angular.
                    Understanding of system design principles including scalability and distributed systems.
                    Knowledge of databases both SQL and NoSQL. Experience with REST APIs.
                    Problem solving ability demonstrated through competitive programming or projects.
                    Good communication skills and ability to work in a team environment.
                    Responsibilities: Design and implement scalable software solutions.
                    Write clean maintainable code with proper documentation.
                    Participate in code reviews and contribute to team discussions.
                    Work on real products used by millions of users worldwide."""
                },
                {
                    "company_name": "Microsoft",
                    "role_title": "SWE Intern",
                    "required_skills": ["C#","Azure","SQL","Python","OOP"],
                    "cgpa_cutoff": 7.0,
                    "jd_text": """Microsoft Software Engineer Intern
                    Requirements: Proficiency in C++ Python or C#. Strong OOP concepts.
                    Understanding of Azure cloud services. Knowledge of SQL databases.
                    Experience with version control using Git. Familiarity with Agile methodology.
                    Strong analytical and problem-solving skills.
                    Responsibilities: Develop features for Microsoft products.
                    Collaborate with senior engineers on architecture decisions.
                    Write unit tests and integration tests. Participate in sprint planning."""
                },
                {
    "company_name": "Infosys",
    "role_title": "Systems Engineer",
    "required_skills": ["Java", "Python", "SQL", "OOP"],
    "cgpa_cutoff": 6.5,
    "jd_text": """Infosys Systems Engineer - Campus 2026
    Requirements: B.Tech/BE in any discipline with 65% or above.
    Strong programming fundamentals in Java or Python.
    Knowledge of databases and SQL queries.
    Good analytical and problem solving skills.
    Basic understanding of software development lifecycle.
    Team player with good communication skills."""
},
            ]

            jd_objects = []
            for jd_data in jds_data:
                jd = JobDescription(**jd_data)
                db.add(jd)
                jd_objects.append(jd)
            db.commit()
            print("✅ JDs seeded")

            # Embed JDs for RAG
            print("Embedding JDs for RAG (takes ~1 minute first time)...")
            for jd in jd_objects:
                embed_jd(str(jd.id), jd.jd_text)

        print("\n✅ Seed complete!")
        print("\nTest credentials:")
        print("  Student:  rahul@college.edu / password123")
        print("  Officer:  officer@college.edu / officer123")

    finally:
        db.close()

if __name__ == "__main__":
    seed()
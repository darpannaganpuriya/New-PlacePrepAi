import json, asyncio
from groq import AsyncGroq
from services.rag_service import retrieve_jd_context
from services.session_service import SessionManager
from core.config import settings

client = AsyncGroq(api_key=settings.GROQ_API_KEY)

WARMUP_QUESTIONS = [
    "Tell me about yourself — your background, skills, and what drew you to this field.",
    "Walk me through your most significant project — what did you build, what was your role, and what was the biggest challenge?"
]

CLOSING_QUESTIONS = [
    "What is one technical decision you made in a project that you would do differently now, and why?",
    "Where do you see yourself growing technically in the next two years, and what steps are you taking toward that?"
]

FILLERS = ["um", "uh", "like", "you know", "basically", "literally", "so"]


class InterviewAgent:

    def __init__(self, session_id: str, student: dict, jd: dict):
        self.session_id = session_id
        self.student = student
        self.jd = jd
        self.session = SessionManager(session_id)
        self.session.init(
            student_name=student["name"],
            jd_id=str(jd["id"]),
            jd_role=jd["role_title"]
        )

    async def get_opening_question(self) -> str:
        return WARMUP_QUESTIONS[0]

    async def get_next_question(self, student_answer: str) -> dict:
        state = self.session.get_state()
        q_count = state.get("questions_asked", 0)
        elapsed = self.session.elapsed_minutes()

        memory = self.session.get_memory()
        last_q = memory[-1]["q"] if memory else WARMUP_QUESTIONS[0]
        self.session.add_exchange(last_q, student_answer)

        if self._should_end(q_count, elapsed):
            return {
                "question": None,
                "phase": "complete",
                "questions_asked": q_count,
                "session_complete": True,
                "feedback": None
            }

        new_count = self.session.increment_question()

        if new_count <= len(WARMUP_QUESTIONS):
            question = WARMUP_QUESTIONS[new_count - 1]
            phase = "warmup"
        elif new_count >= settings.MAX_QUESTIONS - 1:
            idx = min(
                new_count - (settings.MAX_QUESTIONS - 1),
                len(CLOSING_QUESTIONS) - 1
            )
            question = CLOSING_QUESTIONS[idx]
            phase = "closing"
        else:
            question = await self._technical_question(student_answer, elapsed)
            phase = "technical"

        feedback = None
        if new_count % settings.FEEDBACK_EVERY_N == 0:
            feedback = await self._live_feedback(student_answer)

        return {
            "question": question,
            "phase": phase,
            "questions_asked": new_count,
            "session_complete": False,
            "feedback": feedback
        }

    async def _technical_question(self, answer: str, elapsed: float) -> str:
        if not answer or answer.strip() in ["", "(no answer recorded)"]:
            answer = "The candidate gave a verbal answer that was not captured clearly."

        jd_context = await retrieve_jd_context(answer, str(self.jd["id"]))
        history = self.session.memory_as_text()
        state = self.session.get_state()
        q_num = state.get("questions_asked", 0)
        skills = self.student.get("skills", [])
        skills_str = ", ".join(skills[:5]) if skills else "programming"

        prompt = f"""You are a senior technical interviewer conducting a real placement interview.

ROLE YOU ARE HIRING FOR: {self.jd['role_title']}
COMPANY TYPE: {self.jd['company_name']}

CANDIDATE INFO:
- Name: {self.student['name']}
- Branch: {self.student.get('branch', 'CS')}, CGPA: {self.student.get('cgpa', 'N/A')}
- Skills on resume: {skills_str}

ROLE REQUIREMENTS:
{jd_context}

CONVERSATION SO FAR:
{history}

CURRENT STATE: Question {q_num}/12 | Time elapsed: {elapsed:.0f} minutes
{"IMPORTANT: Only 2 questions left. Ask closing/wrap-up questions now." if elapsed > 18 else ""}

YOUR TASK — Choose the BEST next question based on conversation history:

If question 1-2: Ask specific intro tied to their skills and this role
If question 3-6: Go DEEP on technical topics specific to {self.jd['role_title']}
  - Ask about SPECIFIC technologies from their skills: {skills_str}
  - Ask about real project challenges and decisions made
  - Ask about core concepts for this role
If question 7-10: Mix technical depth with problem-solving scenarios
If question 11-12: Behavioral and growth mindset questions

ROLE-SPECIFIC QUESTION BANK — pick from these topics based on role:
- For JavaScript/Frontend: closures, promises, React hooks, virtual DOM, CSS flexbox, state management, lazy loading, TypeScript types
- For ML Engineer: bias-variance tradeoff, cross-validation, gradient descent, regularization, CNN architecture, model deployment, feature selection
- For AI/GenAI Engineer: RAG pipeline, chunking strategies, embedding models, prompt chaining, LLM temperature, hallucination mitigation, fine-tuning vs RAG
- For Data Engineer: window functions in SQL, ETL vs ELT, Spark partitioning, DAG design in Airflow, data modeling, incremental loads, data quality checks
- For Data Scientist: p-value interpretation, overfitting detection, A/B test design, confusion matrix, feature importance, EDA steps, model selection
- For Data Analyst: SQL GROUP BY vs HAVING, pivot tables, KPI definition, cohort analysis, dashboard design choices, outlier handling

NEVER ask:
- "Tell me about yourself" (already asked in warmup)
- "Walk me through a project" (already asked in warmup)
- Any question already in the conversation history above
- Generic questions not related to {self.jd['role_title']}

STRICT FORMAT:
- ONE question only, maximum 2 sentences
- No question numbers, no "Great answer!", no "Certainly"
- Reference their actual work or previous answer when possible
- Sound like a real human interviewer

Output ONLY the question. Nothing else."""

        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Candidate just said: \"{answer[:300]}\"\n\nAsk the next specific question for {self.jd['role_title']} role:"}
                ],
                max_tokens=150,
                temperature=0.85
            )
            question = response.choices[0].message.content.strip().strip('"')
            print(f"✅ Groq generated: {question[:80]}")
            return question
        except Exception as e:
            print(f"❌ Groq error: {e}")
            fallbacks = [
                f"Can you explain how you have used {skills[0] if skills else 'Python'} in a real project?",
                f"What is the most complex technical problem you solved related to {self.jd['role_title']}?",
                "Walk me through how you debug a difficult issue in your code.",
                "How do you stay updated with the latest developments in your field?",
                "Describe a time you had to learn a new technology quickly for a project.",
            ]
            return fallbacks[q_num % len(fallbacks)]

    async def _live_feedback(self, answer: str) -> dict:
        vision = self.session.vision_summary()
        full = self.session.get_full_answer()
        words = full.split()
        filler_count = sum(full.lower().count(f) for f in FILLERS)
        wpm = round(len(words) / max(1, len(words) / 130))

        prompt = """You are a supportive interview coach giving quick live feedback.
Be encouraging but specific. Maximum 2 sentences.
Return ONLY valid JSON (no markdown):
{"tip": "your tip here", "tone": "positive"}
tone must be one of: positive, neutral, needs_work"""

        user_msg = f"""Recent answer: "{answer}"
Stats: {wpm} WPM (ideal 120-150), {filler_count} filler words.
Eye contact: {vision['avg_eye_contact']}%, Posture: {vision['avg_posture']}%.
Give one specific coaching tip."""

        try:
            resp = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=80,
                temperature=0.5
            )
            raw = resp.choices[0].message.content.strip()
            return json.loads(raw)
        except Exception:
            return {"tip": "Keep going — you are doing well!", "tone": "positive"}

    async def evaluate_session(self, session_id: str) -> dict:
        from models.database import SessionLocal, SessionTranscript, JobDescription

        db = SessionLocal()
        try:
            rows = db.query(SessionTranscript).filter(
                SessionTranscript.session_id == session_id
            ).order_by(SessionTranscript.turn_number).all()

            if not rows:
                print(f"No transcript rows found for session {session_id}")
                return self._default_scores()

            transcript_text = "\n".join(
                f"Q{r.turn_number}: {r.question}\nA{r.turn_number}: {r.answer}"
                for r in rows
            )

            jd = db.query(JobDescription).filter(
                JobDescription.id == self.jd["id"]
            ).first()
            jd_text = jd.jd_text if jd else "No JD available"
            vision = self.session.vision_summary()

        finally:
            db.close()

        prompt = """You are a senior technical interviewer evaluating a mock interview.
Return ONLY valid JSON. No markdown. No explanation. Just the JSON object.

Required format:
{
  "technical_score": 0-100,
  "communication_score": 0-100,
  "body_language_score": 0-100,
  "overall_score": 0-100,
  "verdict": "Excellent|Good|Needs Work|Poor",
  "topics": [{"name": "topic", "score": 0-100, "feedback": "one sentence"}],
  "strengths": ["strength 1", "strength 2", "strength 3"],
  "improvements": ["area 1", "area 2", "area 3"],
  "tips": ["tip 1", "tip 2", "tip 3"]
}"""

        user_msg = f"""JD Requirements:
{jd_text}

Full Interview Transcript:
{transcript_text}

Vision Data: Eye contact {vision['avg_eye_contact']}%, Posture {vision['avg_posture']}%.

Evaluate this candidate honestly and return the JSON."""

        try:
            resp = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=800,
                temperature=0.3
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            print(f"Evaluation error: {e}")
            return self._default_scores()

    def _default_scores(self) -> dict:
        return {
            "technical_score": 70,
            "communication_score": 70,
            "body_language_score": 70,
            "overall_score": 70,
            "verdict": "Good",
            "topics": [
                {"name": "Technical Knowledge", "score": 70, "feedback": "Showed basic understanding"},
                {"name": "Communication", "score": 70, "feedback": "Clear responses"},
                {"name": "Problem Solving", "score": 70, "feedback": "Reasonable approach"}
            ],
            "strengths": [
                "Completed the full interview",
                "Answered all questions",
                "Maintained composure throughout"
            ],
            "improvements": [
                "Provide more specific technical examples",
                "Structure answers using STAR method",
                "Practice speaking at a steady pace"
            ],
            "tips": [
                "Practice mock interviews regularly",
                "Review core concepts before interviews",
                "Prepare 3-4 strong project stories to share"
            ]
        }

    async def match_resume_to_jds(self, resume_text: str, jds: list) -> list:
        jd_list = "\n\n".join(
            f"JD {i+1} (id: {jd['id']}):\nRole: {jd['role_title']}\nRequirements: {jd['jd_text'][:400]}"
            for i, jd in enumerate(jds)
        )

        prompt = """You are a strict placement advisor matching a student resume to job roles.
Analyze the resume carefully. Only give high match scores if skills genuinely match.
A random document should score 10-30%. A strong match scores 80-95%.
Return ONLY valid JSON. No markdown.

Format:
{
  "ranked": [
    {
      "jd_id": "uuid here",
      "rank": 1,
      "match_pct": 0-100,
      "matching_skills": ["skill1", "skill2"],
      "missing_skills": ["skill1"],
      "advice": "one sentence advice"
    }
  ]
}"""

        user_msg = f"""Student Resume:
{resume_text[:1200]}

Available Job Roles:
{jd_list}

Rank ALL roles by genuine match percentage. Be strict and honest. Return JSON only."""

        try:
            resp = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=800,
                temperature=0.2
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            return result.get("ranked", [])
        except Exception as e:
            print(f"Resume match error: {e}")
            return []

    async def generate_shortlist(self, jd: dict, students: list) -> list:
        student_list = "\n".join(
            f"- ID:{s['id']} Name:{s['name']} CGPA:{s['cgpa']} "
            f"Technical:{s.get('technical_score', 0)} "
            f"Communication:{s.get('communication_score', 0)}"
            for s in students
        )

        prompt = """You are a placement officer's assistant ranking candidates.
Return ONLY valid JSON. No markdown.

Format:
{
  "ranked": [
    {
      "student_id": "uuid",
      "rank": 1,
      "match_score": 0-100,
      "reasoning": "one sentence max"
    }
  ]
}"""

        user_msg = f"""Role: {jd['role_title']} at {jd['company_name']}
Requirements: {jd.get('jd_text', '')[:400]}

Candidates:
{student_list}

Rank them by fit. Return JSON."""

        try:
            resp = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=600,
                temperature=0.3
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            return result.get("ranked", [])
        except Exception as e:
            print(f"Shortlist error: {e}")
            return []

    def check_posture_warning(self, posture_score: int) -> str | None:
        if posture_score < settings.POSTURE_WARNING_THRESHOLD:
            if self.session.can_warn_posture():
                self.session.mark_warned()
                return "Try to sit up straight and face the camera 🙂"
        return None

    def _should_end(self, q_count: int, elapsed: float) -> bool:
        if elapsed >= settings.HARD_CUTOFF_MIN:
            return True
        if q_count >= settings.MAX_QUESTIONS and elapsed >= settings.MIN_MINUTES:
            return True
        if elapsed >= settings.MAX_MINUTES and q_count >= settings.MIN_MINUTES:
            return True
        return False
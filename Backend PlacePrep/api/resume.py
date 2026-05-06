from fastapi import APIRouter, Depends, UploadFile, File, Query
from sqlalchemy.orm import Session
from models.database import get_db, Student, JobDescription
import io, json

router = APIRouter()


@router.post("/api/resume/upload")
async def upload_resume(
    resume: UploadFile = File(...),
    student_id: str = Query(default=None),
    db: Session = Depends(get_db)
):
    content = await resume.read()
    resume_text = _extract_text(content)

    # Validate it's a real resume
    if not resume_text or len(resume_text.strip()) < 50 or resume_text == "Could not extract resume text":
        return {
            "status": "error",
            "message": "Could not read resume. Please upload a proper PDF.",
            "suggestions": []
        }

    # Save resume text
    if student_id:
        db.query(Student).filter(Student.id == student_id).update({
            "resume_text": resume_text[:3000]
        })
        db.commit()

    # Get all active JDs
    jds = db.query(JobDescription).filter(
        JobDescription.is_active == True
    ).all()

    if not jds:
        return {"status": "ready", "suggestions": [], "message": "No JDs available."}

    jd_list = [
        {
            "id": str(jd.id),
            "company_name": jd.company_name,
            "role_title": jd.role_title,
            "jd_text": jd.jd_text
        }
        for jd in jds
    ]

    # Match resume to JDs using Groq
    from groq import AsyncGroq
    from core.config import settings

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    jd_summary = "\n\n".join(
        f"JD_ID: {jd['id']}\nRole: {jd['role_title']}\nRequirements: {jd['jd_text'][:300]}"
        for jd in jd_list
    )

    system_prompt = """You are a strict resume-to-job matcher. 
Your job is to analyze the resume carefully and match it to job descriptions based on ACTUAL skills present.

RULES:
- Only give high match % if the resume ACTUALLY has the required skills
- A random document or non-tech resume should get very low scores (10-30%)
- A CS/tech resume should get scores based on skill overlap
- Be honest and strict — don't give everyone 70%+

Return ONLY valid JSON, no markdown:
{
  "ranked": [
    {
      "jd_id": "exact uuid",
      "rank": 1,
      "match_pct": 85,
      "matching_skills": ["Python", "React"],
      "missing_skills": ["Docker", "AWS"],
      "advice": "Strong Python and ML skills match well with this role."
    }
  ]
}"""

    user_msg = f"""Analyze this resume and match to job descriptions:

RESUME TEXT:
{resume_text[:1500]}

JOB DESCRIPTIONS:
{jd_summary}

Instructions:
- Extract skills from resume first
- Compare with each JD requirements
- Give honest match percentages
- If resume has no relevant tech skills, scores should be 10-30%
- Return all {len(jd_list)} JDs ranked

Return JSON only."""

    try:
        resp = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=800,
            temperature=0.2
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        ranked = result.get("ranked", [])
        print(f"✅ Resume matched: {len(ranked)} JDs")
        for r in ranked:
            print(f"  {r.get('jd_id','?')[:8]}... → {r.get('match_pct')}%")
    except Exception as e:
        print(f"❌ Resume match error: {e}")
        ranked = []

    # Build suggestions — only show JDs with >40% match
    suggestions = []
    for m in ranked:
        jd = next((j for j in jd_list if j["id"] == m.get("jd_id")), None)
        if not jd:
            continue
        match_pct = m.get("match_pct", 0)
        suggestions.append({
            "company": jd["company_name"],
            "role": jd["role_title"],
            "matchScore": match_pct,
            "status": "Best Match" if match_pct >= 80
                     else ("Good Match" if match_pct >= 60
                           else ("Possible Match" if match_pct >= 40
                                 else "Low Match")),
            "matchedSkills": m.get("matching_skills", []),
            "missingSkills": m.get("missing_skills", []),
            "reasoning": m.get("advice", ""),
            "jdId": jd["id"]
        })

    # Sort by match score
    suggestions.sort(key=lambda x: x["matchScore"], reverse=True)

    return {"status": "ready", "suggestions": suggestions}


@router.get("/api/resume/jds")
async def get_jds(db: Session = Depends(get_db)):
    jds = db.query(JobDescription).filter(JobDescription.is_active == True).all()
    return [
        {
            "id": str(jd.id),
            "company": jd.company_name,
            "role": jd.role_title
        }
        for jd in jds
    ]


def _extract_text(pdf_bytes: bytes) -> str:
    # Try pdfplumber first (better extraction)
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(
                page.extract_text() or ""
                for page in pdf.pages
            )
        if text.strip():
            print(f"✅ PDF extracted with pdfplumber: {len(text)} chars")
            return text[:3000]
    except Exception as e:
        print(f"pdfplumber failed: {e}")

    # Fallback to PyPDF2
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        text = " ".join(
            page.extract_text() or ""
            for page in reader.pages
        )
        if text.strip():
            print(f"✅ PDF extracted with PyPDF2: {len(text)} chars")
            return text[:3000]
    except Exception as e:
        print(f"PyPDF2 failed: {e}")

    # Try plain text (docx or txt uploaded)
    try:
        text = pdf_bytes.decode("utf-8", errors="ignore")
        if len(text.strip()) > 50:
            print(f"✅ Extracted as plain text: {len(text)} chars")
            return text[:3000]
    except Exception:
        pass

    print("❌ Could not extract any text from file")
    return "Could not extract resume text"
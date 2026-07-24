from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.base import CamelModel

UserRole = Literal["student", "tutor", "admin"]
HealthStatus = Literal["excellent", "good", "fair", "weak", "critical"]
NotificationKind = Literal["info", "success", "warning", "risk", "ai"]


class UserOut(CamelModel):
    id: str
    name: str
    email: str
    role: UserRole
    avatar: str | None = None
    institution_id: str
    grade_id: str | None = None
    board_id: str | None = None


class RoleOption(CamelModel):
    role: UserRole
    label: str
    description: str


class LoginRequest(CamelModel):
    email: EmailStr
    password: str
    institution_code: str


class SelectRoleRequest(CamelModel):
    email: EmailStr
    role: UserRole


class TokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginAuthenticated(CamelModel):
    type: Literal["authenticated"] = "authenticated"
    email: str
    role: UserRole
    user: UserOut
    access_token: str
    institution_code: str
    institution_name: str


class LoginRoleSelection(CamelModel):
    type: Literal["role_selection"] = "role_selection"
    email: str
    roles: list[RoleOption]
    institution_code: str
    institution_name: str


class InstitutionOut(CamelModel):
    id: str
    name: str
    code: str
    type: str
    board_ids: list[str] = []


class CenterOut(CamelModel):
    id: str
    name: str
    city: str
    student_count: int
    batch_count: int


class CenterCreate(CamelModel):
    name: str
    city: str = ""


class CenterUpdate(CamelModel):
    name: str | None = None
    city: str | None = None


class StudentSummaryOut(CamelModel):
    id: str
    name: str
    grade: str
    health: int
    status: HealthStatus
    readiness: int
    last_assessment: str
    critical_gaps: int
    improving: bool
    board: str | None = None
    batch: str | None = None
    center_id: str | None = None
    academic_year: str | None = None


class StudentMasterOut(CamelModel):
    id: str
    name: str
    board: str
    grade: str
    batch: str
    batch_ids: list[str] = []
    center_id: str
    academic_year: str
    school_name: str | None = None
    email: str | None = None
    status: Literal["active", "inactive"]


class StudentCreate(CamelModel):
    name: str
    board: str
    grade: str
    batch: str = ""
    batch_id: str | None = None
    center_id: str = ""
    academic_year: str = "2025-26"
    email: str | None = None
    school_name: str | None = None


class StudentUpdate(CamelModel):
    name: str | None = None
    board: str | None = None
    grade: str | None = None
    batch: str | None = None
    batch_ids: list[str] | None = None
    center_id: str | None = None
    status: Literal["active", "inactive"] | None = None


class TutorBatchOut(CamelModel):
    id: str
    name: str
    board: str
    grade: str
    subject: str | None = None
    schedule_timing: str | None = None
    student_ids: list[str] = []
    avg_score: int | None = None


class TutorBatchCreate(CamelModel):
    name: str
    board: str
    grade: str
    subject: str | None = None
    schedule_timing: str | None = None
    student_ids: list[str] = Field(default_factory=list)


class TutorBatchUpdate(CamelModel):
    name: str | None = None
    subject: str | None = None
    schedule_timing: str | None = None
    avg_score: int | None = None


class CurriculumTopicOut(CamelModel):
    name: str
    questions: int = 0
    mastery: int = 0


class CurriculumSubjectOut(CamelModel):
    name: str
    topics: list[CurriculumTopicOut] = []


class CurriculumGradeOut(CamelModel):
    grade: str
    subjects: list[CurriculumSubjectOut] = []


class CurriculumBoardOut(CamelModel):
    board: str
    grades: list[CurriculumGradeOut] = []


class AddBoardRequest(CamelModel):
    name: str


class AddGradeRequest(CamelModel):
    board: str
    grade: str


class AddSubjectRequest(CamelModel):
    board: str
    grade: str
    subject: str


class AddTopicRequest(CamelModel):
    board: str
    grade: str
    subject: str
    topic: str


class UpdateBoardRequest(CamelModel):
    board: str
    new_name: str


class UpdateGradeRequest(CamelModel):
    board: str
    grade: str
    new_name: str


class UpdateSubjectRequest(CamelModel):
    board: str
    grade: str
    subject: str
    new_name: str


class UpdateTopicRequest(CamelModel):
    board: str
    grade: str
    subject: str
    topic: str
    new_name: str


class QuestionOut(CamelModel):
    id: str
    board: str
    grade: str
    subject: str
    chapter: str
    topic: str
    difficulty: Literal["easy", "medium", "hard"]
    marks: int
    question_type: Literal["mcq", "short", "long"]
    text: str
    status: Literal["active", "draft"]
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    correct_answer: str | None = None


class QuestionCreate(CamelModel):
    board: str
    grade: str
    subject: str
    chapter: str
    topic: str
    text: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    marks: int = 1
    question_type: Literal["mcq", "short", "long"] = "mcq"
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    correct_answer: str | None = None


class QuestionUpdate(CamelModel):
    text: str | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    marks: int | None = None
    status: Literal["active", "draft"] | None = None
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    correct_answer: str | None = None


class QuestionPaperOut(CamelModel):
    id: str
    name: str
    board: str
    grade: str
    subject: str
    question_ids: list[str]
    topics: list[str]
    total_marks: int
    created_at: str
    created_by: str | None = None
    source: Literal["upload", "custom", "manual"]
    parent_paper_id: str | None = None


class QuestionPaperCreate(CamelModel):
    name: str
    board: str
    grade: str
    subject: str
    question_ids: list[str]
    source: Literal["upload", "custom", "manual"] = "upload"
    parent_paper_id: str | None = None


class QuestionPaperBulkCreate(CamelModel):
    name: str
    questions: list[QuestionCreate]
    source: Literal["upload", "manual"] = "manual"


class CustomPaperCreate(CamelModel):
    name: str
    parent_paper_id: str
    question_ids: list[str]


class QuestionPaperUpdate(CamelModel):
    name: str | None = None
    question_ids: list[str] | None = None


class AssessmentOut(CamelModel):
    id: str
    title: str
    board: str
    grade: str
    subject: str
    scope: Literal["subject", "chapter", "topic"]
    mode: Literal["practice", "assessment"]
    batch_name: str
    question_count: int
    duration_minutes: int
    scheduled_at: str
    status: Literal["draft", "scheduled", "live", "completed"]
    class_avg: int | None = None
    center_ids: list[str] = []
    selected_question_ids: list[str] = []
    assigned_student_ids: list[str] = []
    created_by_tutor_id: str | None = None
    chapter: str | None = None
    topic: str | None = None
    question_paper_id: str | None = None
    paper_coverage: Literal["full", "selected_topics"] | None = None
    selected_topics: list[str] | None = None
    student_submitted: bool = False


class AssessmentCreate(CamelModel):
    title: str
    board: str
    grade: str
    subject: str
    scope: Literal["subject", "chapter", "topic"] = "topic"
    mode: Literal["practice", "assessment"] = "assessment"
    batch_name: str
    question_count: int
    duration_minutes: int = 0
    scheduled_at: str = ""
    status: Literal["draft", "scheduled", "live", "completed"] = "scheduled"
    center_ids: list[str] = Field(default_factory=list)
    selected_question_ids: list[str] = Field(default_factory=list)
    assigned_student_ids: list[str] = Field(default_factory=list)
    chapter: str | None = None
    topic: str | None = None
    question_paper_id: str | None = None
    paper_coverage: Literal["full", "selected_topics"] | None = None
    selected_topics: list[str] | None = None


class AssessmentUpdate(CamelModel):
    title: str | None = None
    status: Literal["draft", "scheduled", "live", "completed"] | None = None
    scheduled_at: str | None = None
    assigned_student_ids: list[str] | None = None


class AssessmentSubmissionCreate(CamelModel):
    answers: list[dict]
    time_spent_min: int = 0


class AssessmentSubmissionOut(CamelModel):
    id: str
    assessment_id: str
    student_id: str
    score: int
    max_score: int
    time_spent_min: int
    submitted_at: str
    status: Literal["attended", "absent", "pending"]


class AttendanceRecordOut(CamelModel):
    student_id: str
    student_name: str
    status: Literal["attended", "absent", "pending"]
    score: int | None = None
    max_score: int | None = None
    time_spent_min: int | None = None
    submitted_at: str | None = None


class NotificationOut(CamelModel):
    id: str
    role: UserRole
    kind: NotificationKind
    title: str
    message: str
    created_at: str
    read: bool
    href: str | None = None


class NotificationCreate(CamelModel):
    role: UserRole
    kind: NotificationKind = "info"
    title: str
    message: str
    href: str | None = None


class TutorDashboardSettingsOut(CamelModel):
    page_title: str
    page_subtitle: str
    page_eyebrow: str
    hero_content: dict
    hero_summary_override: dict | None = None
    updated_at: str


class TutorDashboardSettingsUpdate(CamelModel):
    page_title: str
    page_subtitle: str
    page_eyebrow: str
    hero_content: dict
    hero_summary_override: dict | None = None


class SearchResultItem(CamelModel):
    id: str
    kind: Literal["student", "topic", "question", "paper"]
    title: str
    subtitle: str | None = None
    href: str


class SearchResultsOut(CamelModel):
    query: str
    students: list[SearchResultItem] = []
    topics: list[SearchResultItem] = []
    questions: list[SearchResultItem] = []
    papers: list[SearchResultItem] = []


class TutorCreate(CamelModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr


class TutorUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None


class TutorOut(CamelModel):
    id: str
    name: str
    email: str

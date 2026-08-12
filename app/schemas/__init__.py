from typing import Any, Literal

from pydantic import EmailStr, Field

from app.schemas.base import CamelModel

UserRole = Literal["student", "tutor", "admin", "super_user"]
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
    is_owner: bool = False
    admin_portal: Literal["organization", "branch"] | None = None


class RoleOption(CamelModel):
    role: UserRole
    label: str
    description: str
    admin_portal: Literal["organization", "branch"] | None = None


class LoginRequest(CamelModel):
    email: EmailStr
    password: str
    institution_code: str | None = None


class LoginOrganizationOut(CamelModel):
    id: str
    name: str
    code: str


class PlatformOrganizationOut(CamelModel):
    id: str
    name: str
    code: str
    schema_name: str
    type: str
    is_active: bool = True
    admin_count: int = 0


class PlatformOrganizationOwnerOut(CamelModel):
    name: str
    email: str


class PlatformOrganizationAdminOut(CamelModel):
    id: str
    name: str
    email: str
    is_owner: bool = False


class PlatformOrganizationDetailOut(PlatformOrganizationOut):
    owner: PlatformOrganizationOwnerOut | None = None
    admins: list[PlatformOrganizationAdminOut] = []


class PlatformOrganizationCreate(CamelModel):
    organization_name: str = Field(min_length=1, max_length=255)
    organization_code: str = Field(min_length=2, max_length=64)
    owner_name: str = Field(min_length=1, max_length=255)
    owner_phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    type: str = Field(default="coaching", min_length=1, max_length=32)


class PlatformOrganizationUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = Field(default=None, min_length=1, max_length=32)
    is_active: bool | None = None


class PlatformSuperAdminOut(CamelModel):
    id: str
    email: str
    full_name: str
    is_active: bool = True


class PlatformSuperAdminCreate(CamelModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class PlatformStatsOut(CamelModel):
    total_organizations: int
    total_active_organizations: int
    total_super_admins: int


class SetupStatusOut(CamelModel):
    initialized: bool
    setup_required: bool
    default_organization_code: str


class SetupRequest(CamelModel):
    organization_name: str = Field(min_length=1, max_length=255)
    organization_code: str | None = Field(default=None, min_length=2, max_length=64)
    super_admin_name: str = Field(min_length=1, max_length=255)
    super_admin_phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class InstitutionUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = None


class SelectRoleRequest(CamelModel):
    email: EmailStr
    role: UserRole
    institution_code: str | None = None
    admin_portal: Literal["organization", "branch"] | None = None


class SwitchRoleRequest(CamelModel):
    role: UserRole
    admin_portal: Literal["organization", "branch"] | None = None


class RoleOptionsOut(CamelModel):
    roles: list[RoleOption]
    current_role: UserRole
    current_admin_portal: Literal["organization", "branch"] | None = None


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
    admin_portal: Literal["organization", "branch"] | None = None


class LoginRoleSelection(CamelModel):
    type: Literal["role_selection"] = "role_selection"
    email: str
    roles: list[RoleOption]
    institution_code: str
    institution_name: str


class InstitutionOut(CamelModel):
    id: str
    name: str
    code: str | None = None
    type: str
    board_ids: list[str] = []


class CenterOut(CamelModel):
    id: str
    name: str
    code: str = ""
    city: str
    active: bool = True
    student_count: int
    batch_count: int


class CenterCreate(CamelModel):
    name: str
    code: str = ""
    city: str = ""


class BranchContextOut(CamelModel):
    organization: InstitutionOut
    role: UserRole
    is_owner: bool = False
    is_platform_super_user: bool = False
    can_select_all_branches: bool = False
    accessible_centers: list[CenterOut] = []
    student_center_id: str | None = None


class AdminUserOut(CamelModel):
    id: str
    name: str
    email: str
    is_owner: bool = False
    active: bool = True
    center_ids: list[str] = []
    roles: list[str] = []


class AdminCreate(CamelModel):
    name: str
    phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    center_ids: list[str] = Field(default_factory=list)
    is_owner: bool = False
    also_tutor: bool = False


class AdminUpdate(CamelModel):
    name: str | None = None
    email: EmailStr | None = None
    is_owner: bool | None = None


class AdminBranchAccessUpdate(CamelModel):
    center_ids: list[str] = Field(default_factory=list)


class StaffOut(CamelModel):
    id: str
    name: str
    email: str
    is_owner: bool = False
    active: bool = True
    center_ids: list[str] = []
    roles: list[str] = []


class StaffCreate(CamelModel):
    name: str
    phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_owner: bool = False
    is_branch_admin: bool = False
    is_tutor: bool = False
    center_ids: list[str] = Field(default_factory=list)


class StaffUpdate(CamelModel):
    name: str | None = None
    email: EmailStr | None = None
    is_owner: bool | None = None
    is_branch_admin: bool | None = None
    is_tutor: bool | None = None
    center_ids: list[str] | None = None


class StaffBranchUpdate(CamelModel):
    center_ids: list[str] = Field(default_factory=list)


class CenterUpdate(CamelModel):
    name: str | None = None
    city: str | None = None
    active: bool | None = None


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
    last_csc_interaction_at: str | None = None
    days_until_csc_disable: int | None = None
    last_collected_by_name: str | None = None
    last_collection_guardian_name: str | None = None


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
    last_csc_interaction_at: str | None = None
    disable_reason: str | None = None
    days_until_csc_disable: int | None = None


class StudentMasterStatsOut(CamelModel):
    total: int
    active: int
    inactive: int


class StudentCreate(CamelModel):
    name: str
    board: str
    grade: str
    batch: str = ""
    batch_id: str | None = None
    center_id: str = ""
    academic_year: str = "2025-26"
    phone: str | None = Field(default=None, min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: str | None = None
    school_name: str | None = None


class StudentBulkRow(CamelModel):
    name: str
    phone: str = Field(min_length=10, max_length=15)
    board: str
    grade: str
    batch: str = ""
    center_id: str = ""
    center_name: str = ""
    academic_year: str = "2025-26"
    password: str | None = Field(default=None, min_length=8, max_length=128)
    school_name: str | None = None


class StaffBulkRow(CamelModel):
    name: str
    phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_owner: bool = False
    is_branch_admin: bool = False
    is_tutor: bool = False
    center_ids: list[str] = Field(default_factory=list)
    center_names: list[str] = Field(default_factory=list)


class BulkImportRowResult(CamelModel):
    row: int
    name: str
    success: bool
    id: str | None = None
    error: str | None = None


class BulkImportResult(CamelModel):
    created: int
    failed: int
    results: list[BulkImportRowResult]


class StudentBulkImportIn(CamelModel):
    rows: list[StudentBulkRow]


class StaffBulkImportIn(CamelModel):
    rows: list[StaffBulkRow]


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
    available_until: str = ""
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
    timing_over: bool = False
    access_request_status: Literal["pending", "approved", "rejected"] | None = None
    can_attend: bool = False


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
    available_until: str = ""
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
    scheduled_at: str | None = None
    available_until: str | None = None
    assigned_student_ids: list[str] | None = None


class AssessmentAccessRequestCreate(CamelModel):
    reason: str = ""


class AssessmentAccessRequestReview(CamelModel):
    status: Literal["approved", "rejected"]
    review_notes: str | None = None
    extension_days: int = 3


class AssessmentAccessRequestOut(CamelModel):
    id: str
    assessment_id: str
    assessment_title: str
    student_id: str
    student_name: str
    reason: str
    status: Literal["pending", "approved", "rejected"]
    requested_at: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    access_granted_until: str | None = None


class InstitutionPoliciesOut(CamelModel):
    assessment: dict[str, Any]
    csc: dict[str, Any]


class InstitutionPoliciesUpdate(CamelModel):
    assessment: dict[str, Any] | None = None
    csc: dict[str, Any] | None = None


class ReportCollectionCreate(CamelModel):
    report_kind: Literal["assessment", "overall", "monthly"]
    report_ref: str = ""
    collected_at: str = ""
    guardian_name: str | None = None
    notes: str | None = None


class ReportCollectionOut(CamelModel):
    id: str
    student_id: str
    report_kind: Literal["assessment", "overall", "monthly"]
    report_ref: str
    collected_at: str
    collected_by_user_id: str
    collected_by_name: str
    guardian_name: str | None = None
    notes: str | None = None


class StudentExamAttendanceOut(CamelModel):
    assessment_id: str
    assessment_title: str
    subject: str
    submitted_at: str
    score: int
    max_score: int
    accuracy_pct: int
    time_spent_min: int
    status: str


class StudentAccessRequestOut(CamelModel):
    id: str
    assessment_id: str
    assessment_title: str
    student_id: str
    reason: str = ""
    status: Literal["pending", "approved", "rejected"]
    requested_at: str
    reviewed_by: str | None = None
    reviewed_by_name: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    access_granted_until: str | None = None


class StudentTrackingOut(CamelModel):
    student_id: str
    student_name: str
    last_csc_interaction_at: str | None = None
    days_until_csc_disable: int | None = None
    last_collected_by_name: str | None = None
    last_collection_guardian_name: str | None = None
    exam_attendances: list[StudentExamAttendanceOut] = []
    report_collections: list[ReportCollectionOut] = []
    access_requests: list[StudentAccessRequestOut] = []


class StudentAssessmentSummaryOut(CamelModel):
    assessment_id: str
    assessment_title: str
    subject: str
    submitted_at: str
    accuracy: int
    student_message_en: str
    student_message_ta: str
    csc_referral_en: str = "For a detailed report, please visit your CSC center."
    csc_referral_ta: str = "விரிவான அறிக்கைக்கு CSC மையத்தை அணுகவும்."


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
    user_id: str | None = None
    role: UserRole
    type: str = "general"
    kind: NotificationKind
    title: str
    message: str
    entity_type: str | None = None
    entity_id: str | None = None
    created_at: str
    read: bool
    href: str | None = None


class NotificationCreate(CamelModel):
    role: UserRole
    kind: NotificationKind = "info"
    type: str = "general"
    title: str
    message: str
    href: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None


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
    phone: str = Field(min_length=10, max_length=15)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    also_admin: bool = False
    center_ids: list[str] = Field(default_factory=list)
    is_owner: bool = False


class TutorUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None


class TutorOut(CamelModel):
    id: str
    name: str
    email: str

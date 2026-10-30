"""Databasemodellen, delt i ét modul pr. domæne.

Observationer er normaliseret i observers/trips/observations, så hele arkivet (2010→) kan
ligge i én database. Geometrier ligger på lokaliteter og områder og bruges kun via PostGIS-SQL.

Alt re-eksporteres her, så `from portal.models import X` virker uanset hvilket modul tabellen ligger i.
Hvilken databasegruppe en tabel hører til, står i portal/dbgroups.py.
"""

from portal.models.ddl import EXTRA_DDL
from portal.models.reference import (
    Species,
    SpeciesCategory,
    CategoryOverride,
    ManualFind,
    Afdeling,
    Kommune,
    Location,
    BemaerkThreshold,
    PhenologyWindow,
    ReferenceEdit,
    SpeciesEdit,
    CodeText,
)
from portal.models.observations import Observer, Trip, Observation, DaySync, ArchiveFile
from portal.models.aggregates import (
    SpeciesDay,
    EffortDay,
    PhenoMetric,
    SpeciesSeason,
    ClimateMonth,
    AreaSpeciesCount,
    LocSpeciesFinds,
    RegionYearSpecies,
    RegionReach,
    RegionTotals,
    LocationYear,
)
from portal.models.users import (
    Role,
    User,
    UserLocation,
    PushSubscription,
    Area,
    AlertRule,
    ThreadFollow,
    ThreadComment,
    ThreadStatus,
    Notification,
    ListGroup,
    ListGroupMember,
    Setting,
    NewsPost,
    MobPreset,
)
from portal.models.articles import (
    ArtImport,
    ContentMedia,
    ArtArticle,
    ArtTag,
    ArtArticleTag,
    ArtSpecies,
    ArtComment,
    ArtCommentReport,
    ArtSubscription,
    ArtRevision,
)
from portal.models.learning import Course, Question, QuestionProgress, QuizResult
from portal.models.field import FieldTrip, FieldBreedingPair, FieldDisturbance, FieldReport, FieldReportUpdate
from portal.models.matrikel import Matrikel, MatrikelPeriode
from portal.models.social import Like, Activity, SupportThread, SupportMessage, CommentBan
from portal.models.datastatus import MonthSync, WeatherMonth, WeatherCellDay
from portal.models.events import DofEvent, SiteCalendar, CalendarVisit
from portal.models.dofbasen import (
    DofbPage,
    DofbNews,
    DofbFocus,
    DofbList,
    DofbArea,
    DofbRole,
    DofbUserRole,
    DofbApiGrant,
    DofbApiToken,
    DofbObsStatus,
    DofbAudit,
)
from portal.models.netfugl import (
    NfPage,
    NfArticle,
    NfComment,
    NfPhoto,
    NfLike,
    NfSpecies,
    NfFamily,
    NfLink,
    NfFeed,
    NfFeedItem,
    NfKryds,
    NfAutoList,
    NfRole,
    NfUserRole,
    NfApiGrant,
    NfApiToken,
    NfAudit,
)
from portal.models.dku import DkuMember, DkuReview, DkuCandidate, DkuCase, DkuParticipant, DkuMessage

__all__ = ["EXTRA_DDL", "Species", "SpeciesCategory", "CategoryOverride", "ManualFind", "Afdeling", "Kommune", "Location", "BemaerkThreshold", "PhenologyWindow", "ReferenceEdit", "SpeciesEdit", "CodeText", "Observer", "Trip", "Observation", "DaySync", "ArchiveFile", "SpeciesDay", "EffortDay", "PhenoMetric", "SpeciesSeason", "ClimateMonth", "AreaSpeciesCount", "LocSpeciesFinds", "RegionYearSpecies", "RegionReach", "RegionTotals", "LocationYear", "Role", "User", "UserLocation", "PushSubscription", "Area", "AlertRule", "ThreadFollow", "ThreadComment", "ThreadStatus", "Notification", "ListGroup", "ListGroupMember", "Setting", "NewsPost", "MobPreset", "Course", "Question", "QuestionProgress", "QuizResult", "FieldTrip", "FieldBreedingPair", "FieldDisturbance", "FieldReport", "FieldReportUpdate", "Matrikel", "MatrikelPeriode", "Like", "Activity", "SupportThread", "SupportMessage", "CommentBan", "MonthSync", "WeatherMonth", "WeatherCellDay", "DofEvent", "SiteCalendar", "CalendarVisit", "DofbPage", "DofbNews", "DofbFocus", "DofbList", "DofbArea", "DofbRole", "DofbUserRole", "DofbApiGrant", "DofbApiToken", "DofbObsStatus", "DofbAudit", "NfPage", "NfArticle", "NfComment", "NfPhoto", "NfLike", "NfSpecies", "NfFamily", "NfLink", "NfFeed", "NfFeedItem", "NfKryds", "NfAutoList", "NfRole", "NfUserRole", "NfApiGrant", "NfApiToken", "NfAudit", "DkuMember", "DkuReview", "DkuCandidate", "DkuCase", "DkuParticipant", "DkuMessage", "ArtImport", "ContentMedia", "ArtArticle", "ArtTag", "ArtArticleTag", "ArtSpecies", "ArtComment", "ArtCommentReport", "ArtSubscription", "ArtRevision"]

"""Client tích hợp SDLC_Harness cho Niko.

Package này là ranh giới mỏng: Niko chỉ gọi CLI/status và đọc JSON, không tự
load fixture/Jira/dataset của workflow SDLC.
"""

from niko.sdlc.client import (
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_START,
    ROUTE_SDLC_START_JIRA,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcClient,
    SdlcRequest,
    SdlcResult,
    detect_sdlc_request,
    sdlc_enabled,
)
from niko.sdlc.jobs import SdlcJob, SdlcJobRunner

__all__ = [
    "ROUTE_SDLC_APPROVE",
    "ROUTE_SDLC_CONTINUE",
    "ROUTE_SDLC_START",
    "ROUTE_SDLC_START_JIRA",
    "ROUTE_SDLC_STATUS",
    "ROUTE_SDLC_SYNC",
    "SdlcClient",
    "SdlcRequest",
    "SdlcResult",
    "SdlcJob",
    "SdlcJobRunner",
    "detect_sdlc_request",
    "sdlc_enabled",
]

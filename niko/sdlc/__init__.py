"""Client tích hợp SDLC_Harness cho Niko.

Package này là ranh giới mỏng: Niko chỉ gọi CLI/status và đọc JSON, không tự
load fixture/Jira/dataset của workflow SDLC.
"""

from niko.sdlc.client import (
    SdlcClient,
    SdlcRequest,
    SdlcResult,
    detect_sdlc_request,
    sdlc_enabled,
)

__all__ = ["SdlcClient", "SdlcRequest", "SdlcResult", "detect_sdlc_request", "sdlc_enabled"]

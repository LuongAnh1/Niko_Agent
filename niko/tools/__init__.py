"""Tool adapters dùng chung cho Niko Loop.

Package này chứa các adapter theo contract `niko.loop.Tool`. Domain vẫn sở hữu
dữ liệu của mình: memory store nằm ở `niko.memory`, Jira/API hoặc fixture nằm ở
tool Jira tương ứng. Graph gọi Loop/ToolRegistry để điều phối workflow, còn tool
chỉ fetch/normalize dữ liệu hoặc mutate khi domain cho phép.
"""

__all__: list[str] = []

import os
from langfuse import Langfuse

class Lumi(Langfuse):
    def __init__(
        self, 
        public_key: str = None, 
        secret_key: str = None, 
        host: str = None, 
        **kwargs
    ):
        # 1. 优先级：代码参数 > 环境变量 (LUMI_xxx) > 默认内网地址
        actual_host = host or os.getenv("LUMI_HOST", "http://192.168.x.x:3000")
        actual_public_key = public_key or os.getenv("LUMI_PUBLIC_KEY")
        actual_secret_key = secret_key or os.getenv("LUMI_SECRET_KEY")

        # 2. 注入回 Langfuse 需要的环境变量名，确保底层逻辑能识别
        os.environ["LANGFUSE_HOST"] = actual_host
        if actual_public_key:
            os.environ["LANGFUSE_PUBLIC_KEY"] = actual_public_key
        if actual_secret_key:
            os.environ["LANGFUSE_SECRET_KEY"] = actual_secret_key

        # 3. 调用父类初始化
        super().__init__(
            public_key=actual_public_key,
            secret_key=actual_secret_key,
            host=actual_host,
            **kwargs
        )
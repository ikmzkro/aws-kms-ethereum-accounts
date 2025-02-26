#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: MIT-0

from aws_cdk import (Stack,
                     Duration,
                     CfnOutput,
                     BundlingOptions,
                     RemovalPolicy,
                     aws_lambda,
                     aws_kms,
                     DockerImage
                     )
from constructs import Construct


# AWS Lambda 関数を CDK で定義し、デプロイ時に Docker を使って Python の依存関係 (requirements.txt) をインストール
# Lambda のコード (.py ファイル) を適切なフォルダにコピーし、AWS Lambda にデプロイする
class EthLambda(Construct):

    def __init__(self,
                 scope: Construct,
                 id: str,
                 dir: str,
                 env: dict
                 ):
        super().__init__(scope, id)

        commands = [
            "if [[ -f requirements.txt ]]; then pip install --target /asset-output -r requirements.txt; fi",
            "cp --parents $(find . -name '*.py') /asset-output"
        ]

        # AWS Lambda のコードをデプロイする際に ローカル環境ではなく Docker コンテナで処理する 設定
        bundling_config = BundlingOptions(
            image=DockerImage("public.ecr.aws/sam/build-python3.9:latest-x86_64"),
            command=["bash", "-xe", "-c", " && ".join(commands)]
        )

        #  Lambda のコードを CDK に登録
        code = aws_lambda.Code.from_asset(
            path=dir, bundling=bundling_config
        )

        lf = aws_lambda.Function(
            self,
            "Function",
            handler="lambda_function.lambda_handler",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            environment=env,
            timeout=Duration.minutes(2), # 実行時間の最大値（2分）
            code=code,
            memory_size=256 # Lambda 関数のメモリサイズ（256MB）
        )

        self.lf = lf


class AwsKmsLambdaEthereumStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, eth_network: str = 'sepolia', **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. AWS KMS キー (aws_kms.Key)の作成と登録
        # ECC_SECG_P256K1 (Ethereum の ECDSA 鍵) を使用。
        # kms:GetPublicKey と kms:Sign の権限を Lambda 関数に付与。
        cmk = aws_kms.Key(self, "eth-cmk-identity",
                          removal_policy=RemovalPolicy.DESTROY)
        cfn_cmk = cmk.node.default_child
        cfn_cmk.key_spec = 'ECC_SECG_P256K1' # ECDSA, 楕円曲線暗号
        cfn_cmk.key_usage = 'SIGN_VERIFY'    # 署名と検証用に利用する

        # 2. AWS Lambda 関数 (aws_lambda.Function)の呼び出しと権限付与
        # 2つの Lambda 関数 (eth_client と eth_client_eip1559) を作成。
        # それぞれ KMS キーの kms:GetPublicKey と kms:Sign の権限を持つ。(Ethereum秘密鍵を KMS で管理し、安全に署名を行う)
        # Lambda のコードは aws_kms_lambda_ethereum/_lambda/functions/ ディレクトリ内の Python スクリプト。
        eth_client = EthLambda(self, "eth-kms-client",
                               dir="aws_kms_lambda_ethereum/_lambda/functions/eth_client",
                               env={"LOG_LEVEL": "DEBUG",
                                    "KMS_KEY_ID": cmk.key_id,
                                    "ETH_NETWORK": eth_network
                                    }
                               )

        cmk.grant(eth_client.lf, 'kms:GetPublicKey')
        cmk.grant(eth_client.lf, 'kms:Sign')

        eth_client_eip1559 = EthLambda(self, "KmsClientEIP1559",
                                       dir="aws_kms_lambda_ethereum/_lambda/functions/eth_client_eip1559",
                                       env={"LOG_LEVEL": "DEBUG",
                                            "KMS_KEY_ID": cmk.key_id,
                                            "ETH_NETWORK": eth_network
                                            }
                                       )

        cmk.grant(eth_client_eip1559.lf, 'kms:GetPublicKey')
        cmk.grant(eth_client_eip1559.lf, 'kms:Sign')

        # 3. CloudFormation 出力 (CfnOutput)
        # KMS キーの ID (KeyID) を出力。
        CfnOutput(self, 'KeyID', value=cmk.key_id,
                  description="KeyID of the KMS-CMK instance used as the Ethereum identity instance")

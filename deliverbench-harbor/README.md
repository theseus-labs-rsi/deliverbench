# DeliverBench Harbor 公共扩展 0.7.2

为 DeliverBench 项目提供不评分开场、原生会话续接、阶段材料释放、依前序产物生成的回应、冻结、独立 verifier、完整判决校验和计分。

需要 Python 3.12、uv、Docker 与 Compose。依赖原生 `harbor==0.20.0`；不携带 Harbor 源码，不依赖私人镜像。从代码仓库根目录使用独立环境安装：

```sh
uv venv --python 3.12 .venv-deliverbench
uv pip install --python .venv-deliverbench/bin/python uv 'git+https://github.com/theseus-labs-rsi/deliverbench.git@v3.1.1#subdirectory=deliverbench-harbor'
source .venv-deliverbench/bin/activate
harbor --version
harbor plugins list
```

扩展按源码目录分发，尚不以 PyPI 安装为前提。与 Harbor 安装在同一环境后，项目使用 `harbor run -c harbor_job.yaml --plugin deliverbench`；模型和凭据配置见各项目 README。

## 判据与证据协议

Rubric schema 1.1.0 每条固定十字段：`id`、`description`、`weight`、`hurdle`、`target`、`dimension`、`evaluation_aspect`、`artifact_scope`、`depends_on`、`bonus`。权重为整数；完整性与枚举以 `src/deliverbench_harbor/runtime/rubric.schema.json` 为准。

Judge 逐条独立给出命中与证据，不读取其他判决，不计算总分。外部程序验证完整性、同单元依赖和hurdle，再按整数权重汇总。缺判决、不可读证据及运行故障不补成零分。FINAL独立评价实际最终文件，冻结历史只作为参考证据。

业务事实与回应规则位于各项目。Responder 发布的内容与输入哈希绑定，成功回执不可覆盖；技术超时/解析错误与有效空回复分开。隐藏事实与模拟器内部记录不挂载给Candidate。

`deliverbench-check` 校验完成状态及证据，`deliverbench-resume` 处理可恢复的已有运行；具体参数可用 `--help` 查看。`qa/` 提供协议和故障检查，必须在独立工作目录运行。所有测试成绩均不作为正式模型分数。

## 中文主体语言 hurdle

以“语言违规判据（中文主体）：”开头的过程判据使用显式语言观察协议。它检查中文主体是否构成违规，并不要求提交中文。Judge 必须逐份报告 `artifact_scope` 文件的主体语言、正文位置、原文引句和判断依据；具体占比、统计范围及双语例外仍由判据定义。

`chinese_dominant` 才对应 `hit=true / violation_present`；英文正文、少量中文对照及完整英文并列不触发该违规。文件确实缺失由缺交付判据独立处理；读取失败或语言证据不清时须报告 `ungradable`。缺少观察、文件覆盖不全、观察与判决矛盾会被拒绝；批量裁判仅在既有重试上限内重试未接受项，仍失败则不发布数值分数。计分入口也验证同一合同。

0.7.2 为这些判据增加了必填 `language_assessment`，旧版七字段判决不能直接作为新版判决复用。原判决与历史修正应保留在各自实验版本下。该协议验证证据与结论的一致性，不替代对原文件的专业判断，也不保证所有模型判断均正确。

离线回归（不调用模型）：

```sh
python deliverbench-harbor/qa/language_hurdle_checks.py --project data/02_project_mars_refinancing
```

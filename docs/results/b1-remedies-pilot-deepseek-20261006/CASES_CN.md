# 中断试验：B1 与历史 B0 的变化案例

计分边界：初始根 results 保存56题，变化集为6条新增成功、1条回归；场景局部 results 另有 cfc96471 已计分且两版均成功。补入该真实证据后，共57题已计分：B1通过50、同57题B0通过45，共同成功44、共同失败6、新增6、回归1；3题未计分。本报告不给60题最终成功率，不补判未完成任务。

证据：B0仅取冻结generation1；7例共56个原始证据文件及2个前序案例的8个文件均匹配baseline-audit哈希。7例B1及补入的cfc96471均核对worker退出/job、world quiescent、simulation_paused、lease、cutoff。轨迹按同一指令且开始时间差<1秒唯一匹配。完整逐项before/after/checks、工具步号和SHA-256在 [cases.json](./docs/results/b1-remedies-pilot-deepseek-20261006/cases.json)。这是中断快照分析，不宣称整个attempt已封存。

## 最值得保留的发现

- 3条方向题有直接动作证据：新版按航向补足实际航段，不再坚持不可达的净终点。
- Circle Target8两题确有正确目标上的动作，但有题前感知差异；后一题的覆盖100%来自前一题。
- 唯一回归是Circle Target3发现失败，没有覆盖和照片。目标知识差异更早出现在第2题64b6560e，并非始于第7题529c0622。
- 57条已计分轨迹中距离新工具只调用1次（fa30ebff），原距离16.6433m已≤23m，未执行移动；coverage候选恢复回执0次。不能把新增成功直接归功于这两个新纠错分支。

## ad34350a/7eed5422 — Fleet Coordinated Takeoff and Formation (4 Drones) 1

新增成功。原检查12/16→16/16。Drone12北向0→83m、东向0→68m；Drone7北向51→83m；Drone13北向24→83m。B0主要用路径终点调位，最终把障碍内理想终点改成附近可达点。B1工具19/25让Drone12北飞40+43m，20/26让Drone7北飞35+48m，21/27让Drone13北飞30+53m；22–24西移绕障、28–30南移换通道，31–33各东飞68m。各机题前位置不同，但这些新增方向段由本题回执直接证明；支持方向义务执行改善，不能单独归因提示或导航代码。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T153330553673_ad34350a_f9d1e4b5-ad63-48f2-a113-d2987f9d32bb_completed.json)；job `a76e46c2-a927-4966-9f85-36352b1a2f9d`；SHA-256 `cbc8c07fabc86f4e6c058394e707de6f868a0bbd2237ae319e28ad92aec2f167`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ad34350a/agent-traces/toolchains/20261006T141926845364_ad34350a_47a97dcf-fc49-4eee-b28c-a6469db11a36_completed.json)；job `a6c55692-91f0-49dd-a08d-9d9d6587aa58`；SHA-256 `9a8629569e1cc5482f616a006dc6517de2e596776d1038bacaaacd2b343fc683`。

## ad34350a/a53ba7fc — Fleet Coordinated Takeoff and Formation (4 Drones) 2

新增成功。原检查15/16→16/16，唯一翻转为Drone8北向46→78m。B0因理想终点在障碍内停在(613,463)。B1工具13从编队点(480,417)北飞78m到(480,495)，工具19再南移到(480,450)，工具23东飞112m；最终净北移只有33m，但已实际北飞78m。题前Drone8位置两版不同，本题同一编队义务及成功航段都有记录；未用新距离工具。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T153818453706_ad34350a_f42cfdc8-38d4-48eb-bad9-b4058a894819_completed.json)；job `dff6b840-5d06-4c05-aad4-673ade5f664a`；SHA-256 `73425ea9daffb767f3131e166ef0e3df5dabaccbeed5859de74db4836a7a83cf`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ad34350a/agent-traces/toolchains/20261006T142151777495_ad34350a_2690c289-2c43-4b4f-928a-f2ce26b305f1_completed.json)；job `f6d2dcc0-5988-4061-a406-dc58744c6394`；SHA-256 `24b100560ff87b8c3e275ffa7b1c5fe6c512018e0cacb0a886fb5c3af4c24ba0`。

## ef36e27a/2f4c6c85 — Fleet Coordinated Takeoff and Formation (5 Drones) 1

新增成功。原检查19/20→20/20，Drone6东向90→92m。双方该机题前同为(848,601,0)，本题到编队点(467,575)，北飞97m至(467,672)，东92m均受Polygon Obstacle3阻挡。B0工具28只东移到(557,672)，自然语言明确承认差2m。B1工具25含障碍内末点的路径被拒，26改走(467,740)→(559,740)，原回执成功，实际含完整东向92m。这是动作语义差异的强证据；历史对照仍不是严格消融，不把B0 runtime success=True误写成自然语言谎称已补足。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/agent-traces/toolchains/20261003T165417516009_ef36e27a_c9b033dc-8a0e-4abc-b09d-83c6f4515baa_completed.json)；job `92b36c55-adbf-428c-ba69-66df44ca0928`；SHA-256 `b6a5f34f6055bf81e6e42cf2966d6e3992290451327459ed7d5632540f34d865`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ef36e27a/agent-traces/toolchains/20261006T135441577261_ef36e27a_2e73adda-b82c-4d36-be06-21861dec23cd_completed.json)；job `fb8f6fef-20f3-4e51-a468-415372a84783`；SHA-256 `1c412542bd62721066f6a9a798a75b8733369c60df5b732d23037b2ec6ed42f4`。

## ad34350a/c451cd74 — Complete Area Coverage Search

新增成功。原检查0/2→2/2。B0指定Circle Target8覆盖8.587%→8.587%，初次sense黑板无此目标；工具161生成Polygon Target8的计划、162正确执行39点，目标替代造成失分。B1题前Circle8覆盖0%，但工具3黑板已知正确ID87582e48；5生成计划、6持正确句柄尝试受阻、7导航绕行、8完成8点覆盖，题后100%，指定Drone7到达记录9。工具7其实返回success=False/arrival_verified=False、剩余1.0808m，模型仍继续覆盖并外部通过。没有错误句柄候选恢复；目标感知起点不同，不能把收益全部归给精确名称提示或句柄封装。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T154535038737_ad34350a_ebf55676-4058-4045-8cf0-02d0efc7d24c_step_limit.json)；job `a0f649c6-9dcc-4cb9-a262-ec896c8ccf35`；SHA-256 `85ac33cc6913f1a781b736e804e2e1d83cbeacacb6e4dde688475933de613608`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ad34350a/agent-traces/toolchains/20261006T142231387997_ad34350a_819925df-7328-4c1d-9469-7073dec5c2e4_completed.json)；job `f22b381f-7a31-4433-8881-9652119e86e5`；SHA-256 `cc0eee9197d3ff86f77972d4796c1143874f7845f123ab113ac00a838805c8b4`。

## ad34350a/fe787c55 — Dual Drone Area Coverage Search

新增成功。原检查2/5→5/5。B0又改选Polygon Target8，工具9生成、10/11持有效句柄执行，指定Circle8覆盖始终8.587%，指定两机到达都0。B1题前Circle8已100%（前一题c451cd74完成），但指定Drone12/Drone3尚未到达。工具9生成正确双机计划，10/11直接使用返回的调用参数；受阻后继续绕行，15、24各执行剩余3点，题后两机到达记录各5。覆盖阈值是继承的，指定机访问是本题新增的；不得把它记成第二次独立从零覆盖，也没有触发句柄候选纠错。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T154710110725_ad34350a_7d0e4171-0523-47b7-9171-136c4f072891_completed.json)；job `e28a71f2-90bb-49d6-b333-12276bfa431e`；SHA-256 `da3f9965ece078748c42934c395a96582c8518fa1292e35824824cc94485c70b`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ad34350a/agent-traces/toolchains/20261006T142517995231_ad34350a_94c364bd-5fef-4e0b-b05b-ed9984df70e0_completed.json)；job `7d2b5463-ee92-4b51-bb86-751c8543425f`；SHA-256 `4f57f3693bf88df5425419e98dc7bf35557396a2d0d74585d4d1848f59242887`。

## ef36e27a/1c840341 — Obstacle Avoidance Navigation 1

新增成功。原检查3/4→4/4。双方Drone8题前均(997,106,0)、题后均(860,792,0)，唯一翻转是对起点(232,302,25)的访问：B0无记录，B1恰到、closest_distance=0。B0工具13–17先后选择冲突航点或阻挡路线，最后认定起点“封闭不可达”而跳过去目的地。B1工具12–15沿(360,290)→(300,240)→(232,240)→(232,302)逐步通过窄通道，15成功；16再离开、后续绕行到目的地并降落。真实到达反驳旧版那次不可达推断，未使用coverage或距离新工具；不能据此断言某单项提示、路径算法或架构层独立有效。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/agent-traces/toolchains/20261003T164110841532_ef36e27a_60c41908-25d5-4f6a-9837-bb5059bd4697_completed.json)；job `98ffd43e-4013-49d0-a93b-c35125ee2578`；SHA-256 `1b9f584940600e323e216ccf94f3cb7c1bdc3b7af407c8041d90bbffbcaa115c`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ef36e27a/agent-traces/toolchains/20261006T134709429500_ef36e27a_07eb2f5b-0617-443c-a065-dfddf172763a_completed.json)；job `861c3dbe-7889-4b9c-9f29-38e6439e7a46`；SHA-256 `2ec8e64a0928ddd95e56665560a431e199bb391194cdbfb28c129ab8a43b2603`。

## ad34350a/bfc721cc — Coordinated Search Sweep - 3 Drones 2

回归失败。原检查4/4→0/4。B0题前Circle Target3覆盖22.375%，工具3黑板已有IDe5336486，随后2次生成计划、绕行重试、3次拍照，覆盖达100%。B1题前覆盖0%、黑板无该目标；三架指定机的题前坐标却与B0完全相同。B1做123次工具调用（25次take_off、17次navigate_to_and_sense、73次move_to_and_sense），仍未发现目标；没有覆盖路径执行或照片。工具118把Circle Target3名称当target_id请求产生404，不能据此证明实体不存在；before/after均有真实目标(1891,1304)，覆盖始终0。结束自然语言承认不能完成并请求信息，runtime success=True且带TASK DONE，不是自然语言宣称覆盖完成。此为实际任务失败，非内部保守判定。

前序追溯：B0第2题64b6560e工具9南飞200m至(1971.23,1265.64)并感知，`nearby.targets`和`changes.new_targets`首次出现e5336486。B1同题工具7–10改走(1653,1276)→(1310,1071)→(967,866)→(624,661)，没有发现目标。到第7题529c0622前，B0已覆盖22.375%、B1为0%，该题前后均不改变这两个值。因此“529c导致记忆缺失”不成立；更早的共同成功任务已产生不同机会观测。这支持跨题状态混杂，不证明新提示或句柄补救导致回归。

- [B0原始工具轨迹](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T152556666362_ad34350a_681aab96-8309-4c52-a28f-b8052d754060_completed.json)；job `f9c672c7-9857-4d0e-bca0-4f69c1647a26`；SHA-256 `ec0fd15514df27c83ac4ad9618f2d60600b92c3f0f3a80d7e919921f2854f993`。
- [B1原始工具轨迹](./experiments/runs/20261006-134416-b1-remedies60-1ee84ea7/ad34350a/agent-traces/toolchains/20261006T141456980185_ad34350a_b5c59510-8217-4699-bb23-b51262d662d6_completed.json)；job `e1e427ab-add3-4666-a4fe-23e042a18d98`；SHA-256 `3950d9091d9b5fcd758e80799bf2be9b4a41907119f39a0dcc7f3cb45a73ca35`。

## 归因边界

此次比较是B0与“原B1导航修正＋定向工具/提示”整个版本组合。运行日期、模型采样、并发、前题形成的世界及黑板不同，没有消融来定位每项贡献。三个场景不是57个独立样本。以上只把checker与实际动作作为任务结果证据，模型自述用于说明其解释。没有读密钥、启动服务/模型、修改旧证据或算法。

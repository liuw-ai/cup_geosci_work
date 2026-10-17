import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const registryPath = path.join(projectRoot, "data", "organization_registry.json");

const cnpcDisplayNames = [
  "大庆油田有限责任公司",
  "辽河油田分公司",
  "长庆油田分公司",
  "塔里木油田分公司",
  "新疆油田分公司",
  "西南油气田分公司",
  "吉林油田分公司",
  "大港油田分公司",
  "青海油田分公司",
  "华北油田分公司",
  "吐哈油田分公司",
  "冀东油田分公司",
  "玉门油田分公司",
  "浙江油田分公司",
  "中石油煤层气有限责任公司",
  "南方石油勘探开发有限责任公司",
  "中油国际管道公司",
  "勘探开发研究院",
  "中石油深圳新能源研究院有限公司",
  "中国石油集团电能有限公司",
  "大庆石化分公司",
  "吉林石化分公司",
  "抚顺石化分公司",
  "辽阳石化分公司",
  "兰州石化分公司",
  "独山子石化分公司",
  "乌鲁木齐石化分公司",
  "宁夏石化分公司",
  "大连石化分公司",
  "大连西太平洋石油化工有限公司",
  "锦州石化分公司",
  "锦西石化分公司",
  "大庆炼化分公司",
  "哈尔滨石化分公司",
  "广西石化分公司",
  "四川石化分公司",
  "广东石化有限责任公司",
  "中石油云南石化有限公司",
  "大港石化分公司",
  "华北石化分公司",
  "呼和浩特石化分公司",
  "辽河石化分公司",
  "长庆石化分公司",
  "中石油克拉玛依石化有限责任公司",
  "庆阳石化分公司",
  "中国石油独山子石化产品有限责任公司",
  "润滑油公司",
  "中石油（上海）新材料研究院有限公司",
  "蓝海新材料（通州湾）有限责任公司",
  "东北化工销售分公司",
  "西北化工销售分公司",
  "华东化工销售分公司",
  "北京化工销售运营中心",
  "华南化工销售分公司",
  "西南化工销售分公司",
  "北京销售分公司",
  "上海销售分公司",
  "湖北销售分公司",
  "广东销售分公司",
  "云南销售分公司",
  "辽宁销售分公司",
  "吉林销售分公司",
  "黑龙江销售分公司",
  "天津销售分公司",
  "河北销售分公司",
  "山西销售分公司",
  "内蒙古销售分公司",
  "陕西销售分公司",
  "甘肃销售分公司",
  "青海销售分公司",
  "宁夏销售分公司",
  "重庆销售分公司",
  "四川销售分公司",
  "贵州销售分公司",
  "西藏销售分公司",
  "江苏销售分公司",
  "浙江销售分公司",
  "安徽销售分公司",
  "福建销售分公司",
  "江西销售分公司",
  "山东销售分公司",
  "河南销售分公司",
  "湖南销售分公司",
  "广西销售分公司",
  "中石油新疆销售有限公司",
  "中石油海南销售有限责任公司",
  "西部钻探工程有限公司",
  "长城钻探工程有限公司",
  "渤海钻探工程有限公司",
  "川庆钻探工程有限公司",
  "东方地球物理勘探有限责任公司",
  "中油测井公司",
  "海洋工程有限公司",
  "管道局工程有限公司",
  "大庆钻探工程有限公司",
  "中国石油工程建设有限公司",
  "中国寰球工程有限公司",
  "中国昆仑工程有限公司",
  "中石油（北京）项目管理有限公司",
  "中国石油技术开发有限公司",
  "宝鸡石油机械有限责任公司",
  "中国石油集团宝石管业有限公司",
  "济柴动力有限公司",
  "渤海石油装备制造有限公司",
  "装备制造创新中心（北京石油机械有限公司）",
  "石油化工研究院",
  "规划总院",
  "经济技术研究院",
  "工程技术研究院有限公司",
  "安全环保技术研究院有限公司",
  "工程材料研究院有限公司",
  "中石油（北京）数智研究院有限公司",
  "昆仑数智科技有限责任公司",
  "中国石油集团管理干部学院",
  "石油工业出版社有限公司",
  "《中国石油报》社有限公司",
  "中国石油审计服务中心有限公司",
  "中国石油物资有限公司",
  "共享运营有限公司",
  "昆仑物流有限公司",
  "中国华油集团有限公司",
  "云杉泽（北京）电子商务有限公司",
  "昆仑能源有限公司",
  "国际勘探开发有限公司",
  "中国石油集团国际事业有限公司",
  "中油财务有限责任公司",
  "昆仑银行股份有限公司",
  "昆仑信托有限责任公司",
  "昆仑金融租赁有限责任公司",
  "中石油专属财产保险股份有限公司",
  "昆仑保险经纪股份有限公司",
  "中国石油集团昆仑资本有限公司",
];

const sinopecDisplayNames = [
  "胜利油田",
  "中原油田",
  "河南油田",
  "江汉油田",
  "江苏油田",
  "新星石油公司",
  "上海海洋油气分公司",
  "华东石油局",
  "华北石油局",
  "西北石油局",
  "东北石油局",
  "西南石油局",
  "勘探分公司",
  "天然气分公司",
  "胜利石油工程有限公司",
  "中原石油工程有限公司",
  "江汉石油工程有限公司",
  "西南石油工程有限公司",
  "华东石油工程有限公司",
  "地球物理有限公司",
  "石油工程建设有限公司",
  "国际石油工程公司",
  "海洋石油工程公司",
  "经纬有限公司",
  "燕山石化",
  "齐鲁石化",
  "上海石化",
  "金陵石化",
  "茂名石化",
  "天津石化",
  "扬子石化",
  "仪征化纤",
  "镇海炼化",
  "福建炼化",
  "南京化工公司",
  "广州石化",
  "安庆石化",
  "荆门石化",
  "重庆川维化工有限公司",
  "九江石化",
  "石家庄炼化",
  "济南炼化",
  "中韩石化",
  "润滑油公司",
  "北海炼化",
  "塔河炼化公司",
  "化工销售有限公司",
  "宁夏能源化工有限公司",
  "炼油销售有限公司",
  "中科（广东）炼化有限公司",
  "北京石油分公司",
  "天津石油分公司",
  "河北石油分公司",
  "山西石油分公司",
  "上海石油分公司",
  "江苏石油分公司",
  "浙江石油分公司",
  "安徽石油分公司",
  "福建石油分公司",
  "江西石油分公司",
  "山东石油分公司",
  "河南石油分公司",
  "湖北石油分公司",
  "湖南石油分公司",
  "广东石油分公司",
  "广西石油分公司",
  "海南石油分公司",
  "贵州石油分公司",
  "云南石油分公司",
  "销售华北分公司",
  "销售华东分公司",
  "销售华中分公司",
  "销售华南分公司",
  "销售西北分公司",
  "辽宁石油分公司",
  "黑龙江石油分公司",
  "吉林石油分公司",
  "陕西石油分公司",
  "宁夏石油分公司",
  "新疆石油分公司",
  "内蒙古石油分公司",
  "青海石油分公司",
  "甘肃石油分公司",
  "四川石油分公司",
  "重庆石油分公司",
  "西藏石油分公司",
  "燃料油公司",
  "第四建设公司",
  "第五建设公司",
  "第十建设公司",
  "工程建设公司",
  "广州工程公司",
  "上海工程公司",
  "宁波工程公司",
  "南京工程公司",
  "重型起重运输工程公司",
  "洛阳技术研发中心",
  "节能技术服务公司",
  "石油勘探开发研究院",
  "石油化工科学研究院",
  "北京化工研究院",
  "大连石油化工研究院",
  "上海石油化工研究院",
  "安全工程研究院",
  "石油工程技术研究院",
  "石油物探技术研究院",
  "催化剂公司",
  "国际事业公司",
  "石油机械股份有限公司",
  "国际石油勘探公司",
  "联合石化公司",
  "财务公司",
  "百川公司",
  "经济技术研究院",
  "管理干部学院",
  "石化报社",
  "石化出版社",
  "共享服务公司",
  "项目管理公司",
  "审计中心",
  "中国航空油料有限责任公司",
  "中国航油集团石油股份有限公司",
  "中国航油集团物流有限公司",
  "中国航油集团国际控股有限公司",
  "中国航油集团通用航空发展有限公司",
  "北京中航油工程建设有限公司",
  "中航油石化管道有限公司",
  "深圳承远航空油料有限公司",
  "中国航油集团资产发展有限公司",
  "中国航油集团投资有限公司",
  "中国航油集团海南有限公司",
];

const normalize = (value) => String(value).replace(/[“”‘’（）()《》、，。·\s-]/g, "").toLowerCase();
const slug = (value) => normalize(value).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "unit";

const cnpcAliasToId = new Map([
  ["大庆油田有限责任公司", "cnpc-daqing"],
  ["辽河油田分公司", "cnpc-liaohe"],
  ["长庆油田分公司", "cnpc-changqing"],
  ["塔里木油田分公司", "cnpc-tarim"],
  ["新疆油田分公司", "cnpc-xinjiang"],
  ["西南油气田分公司", "cnpc-southwest-gas"],
  ["吉林油田分公司", "cnpc-jilin"],
  ["大港油田分公司", "cnpc-dagang"],
  ["青海油田分公司", "cnpc-qinghai"],
  ["华北油田分公司", "cnpc-huabei"],
  ["吐哈油田分公司", "cnpc-tuha"],
  ["冀东油田分公司", "cnpc-jidong"],
  ["玉门油田分公司", "cnpc-yumen"],
  ["勘探开发研究院", "cnpc-riped"],
  ["东方地球物理勘探有限责任公司", "cnpc-bgp"],
  ["中油测井公司", "cnpc-logging"],
  ["西部钻探工程有限公司", "cnpc-western-drilling"],
  ["长城钻探工程有限公司", "cnpc-greatwall-drilling"],
  ["渤海钻探工程有限公司", "cnpc-bohai-drilling"],
  ["川庆钻探工程有限公司", "cnpc-sichuan-changqing-drilling"],
  ["中国石油工程建设有限公司", "cnpc-engineering-construction"],
  ["中国寰球工程有限公司", "cnpc-hqccec"],
  ["管道局工程有限公司", "cnpc-pipeline-engineering"],
  ["经济技术研究院", "cnpc-economic-research"],
]);

const sinopecAliasToId = new Map([
  ["胜利油田", "sinopec-shengli"],
  ["江汉油田", "sinopec-jianghan"],
  ["石油勘探开发研究院", "sinopec-petroleum-exploration-production-research"],
  ["石油工程技术研究院", "sinopec-petroleum-engineering-research"],
  ["石油机械股份有限公司", "sinopec-petroleum-machinery"],
  ["石油工程建设有限公司", "sinopec-engineering-construction"],
  ["国际石油勘探公司", "sinopec-international-exploration"],
  ["中石化石油工程技术服务股份有限公司", "sinopec-oilfield-service"],
]);

const roleFor = (name) => {
  if (/(研究院|研究中心|技术研发中心|学院|规划总院|经济技术研究院|安全工程研究院|技术研究院)/.test(name)) return "research_institute";
  if (/(油田|油气田|石油局|勘探分公司|天然气分公司)/.test(name) && !/(炼化|石化|销售)/.test(name)) return "upstream_operator";
  return "internal_technical_service";
};

const makeChannel = (prefix, name, url, backupUrl) => ({
  id: `${prefix}-phase2-${slug(name)}`,
  channel_type: "official_announcement",
  official_url: url,
  backup_urls: [backupUrl],
  verification_status: "candidate",
  is_primary: true,
  evidence_note: "官方招聘平台展示单位；已登记但尚未完成该单位法定名称、组织层级或独立入口的逐项补证。此记录不绑定采集 source_id，不自动启用或发布岗位。",
});

const payload = JSON.parse(await fs.readFile(registryPath, "utf8"));
const organizations = payload.organizations;
const byId = new Map(organizations.map((item) => [item.id, item]));
const byName = new Map();
for (const item of organizations) {
  byName.set(normalize(item.canonical_name), item);
  for (const alias of item.aliases || []) byName.set(normalize(alias), item);
}

const added = [];
const mergedAliases = [];
const addOrMerge = (name, group, url, backupUrl, aliasMap, index) => {
  const mappedId = aliasMap.get(name);
  if (mappedId && byId.has(mappedId)) {
    const item = byId.get(mappedId);
    if (!(item.aliases || []).includes(name)) {
      item.aliases = [...(item.aliases || []), name];
      mergedAliases.push({ id: item.id, alias: name });
    }
    return;
  }
  const existing = byName.get(normalize(name));
  if (existing) return;
  const prefix = group === "cnpc" ? "cnpc" : "sinopec";
  const id = `${prefix}-display-${String(index + 1).padStart(3, "0")}-${slug(name)}`;
  if (byId.has(id)) return;
  const item = {
    id,
    canonical_name: name,
    parent_id: group,
    organization_role: roleFor(name),
    industry_path: group === "cnpc" ? "中国石油体系官方招聘展示单位" : "中国石化体系官方招聘展示单位",
    affiliation: group === "cnpc" ? "中国石油体系" : "中国石化体系",
    aliases: [name],
    official_domains: [group === "cnpc" ? "cnpc.com.cn" : "sinopecgroup.com"],
    channels: [makeChannel(prefix, name, url, backupUrl)],
    official_display_name: name,
    identity_status: "official_display_name_pending_legal_normalization",
    notes: "来源于官方招聘平台展示清单；canonical_name 当前保留页面展示名，不等同于已完成工商法定名称核验。Phase 2 仅登记，不启用采集、不改变岗位发布状态。",
  };
  organizations.push(item);
  byId.set(id, item);
  byName.set(normalize(name), item);
  added.push(item);
};

for (let index = 0; index < cnpcDisplayNames.length; index += 1) {
  addOrMerge(cnpcDisplayNames[index], "cnpc", "https://zhaopin.cnpc.com.cn/", "https://www.cnpc.com.cn/", cnpcAliasToId, index);
}
for (let index = 0; index < sinopecDisplayNames.length; index += 1) {
  addOrMerge(sinopecDisplayNames[index], "sinopec", "https://job.sinopec.com/", "https://www.sinopecgroup.com/", sinopecAliasToId, index);
}

// Repair the first Phase 2 migration's fixed channel IDs in place.  Channel
// IDs are globally unique contract keys, so derive a stable ID from the
// organization ID for candidate records that still carry the old value.
const seenChannelIds = new Set();
for (const item of organizations) {
  for (let channelIndex = 0; channelIndex < (item.channels || []).length; channelIndex += 1) {
    const channel = item.channels[channelIndex];
    const candidateChannel = item.identity_status === "official_display_name_pending_legal_normalization";
    if (candidateChannel && (channel.id === "cnpc-phase2-unit" || channel.id === "sinopec-phase2-unit")) {
      channel.id = `${item.id}-channel-${channelIndex + 1}`;
    }
    if (seenChannelIds.has(channel.id)) {
      channel.id = `${item.id}-channel-${channelIndex + 1}`;
    }
    seenChannelIds.add(channel.id);
  }
}

payload.version = Math.max(Number(payload.version) || 1, 2);
const phase2Description = "Phase 2 organization registration records official recruitment display names from the CNPC and Sinopec platform inventories. Candidate channels remain non-crawlable until legal identity and unit-scoped evidence are separately verified.";
const descriptions = String(payload.description || "")
  .split("\n")
  .filter((line) => line !== phase2Description);
payload.description = [...descriptions, phase2Description].join("\n").trim();
await fs.writeFile(registryPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(JSON.stringify({
  before_count: byId.size - added.length,
  after_count: organizations.length,
  added_count: added.length,
  merged_alias_count: mergedAliases.length,
  added_by_group: {
    cnpc: added.filter((item) => item.parent_id === "cnpc").length,
    sinopec: added.filter((item) => item.parent_id === "sinopec").length,
  },
  merged_aliases: mergedAliases,
}, null, 2));

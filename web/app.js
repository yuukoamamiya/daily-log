const state = {
  data: null,
  route: "overview",
  month: (() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  })(),
  loading: false,
  backup: null,
  settings: null,
  categoryView: "parent",
  categoryExpanded: {},
  categoryQuickAdd: null,
  subscriptionEditorOpen: false,
  onboardingOpen: false,
  dayDetailsDate: null,
  organizer: null,
  organizerScope: "unorganized",
  organizerMonth: null,
  organizerDrafts: { transactions: {}, diary: {}, todos: {} },
  organizerSelections: { transactions: {}, diary: {}, todos: {} },
  organizerSuggestions: null,
  organizerReview: null,
  organizerLoading: false,
  searchQuery: "",
  searchReturnRoute: "overview",
  listFilters: { expenseCategory: "", diaryTag: "", todoStatus: "active" },
  bulkSelections: { transaction: {}, diary: {}, todo: {} },
  bulkDrafts: { transaction: "", diary: "", todo: "" },
  bulkPreview: null,
};

const THEMES = new Set(["light", "dark", "system"]);

function normalizeTheme(theme) {
  return THEMES.has(theme) ? theme : "system";
}

function applyTheme(theme) {
  const selectedTheme = normalizeTheme(theme);
  document.documentElement.dataset.theme = selectedTheme;
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = selectedTheme === "dark" ? "#151a17" : "#f4f5f2";
}

applyTheme("system");

const viewRoot = document.querySelector("#view-root");
const pageTitle = document.querySelector("#page-title");
const pageEyebrow = document.querySelector("#page-eyebrow");
const composer = document.querySelector("#composer");
const backdrop = document.querySelector("#drawer-backdrop");
const sidebar = document.querySelector("#sidebar");
const dayDetailPanel = document.querySelector("#day-detail-panel");
let customSelectSequence = 0;

const routeMeta = {
  overview: ["概览", "生活工作台"],
  calendar: ["日历", "日程与生活轨迹"],
  expenses: ["账目", "消费与预算"],
  diary: ["日记", "每日片段"],
  todos: ["待办", "接下来要做的事"],
  organize: ["整理", "补充分类与标签"],
  search: ["搜索", "查找所有生活记录"],
  settings: ["设置", "本机配置与备份"],
};

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function replaceHtml(target, markup) {
  const parsed = new DOMParser().parseFromString(`<body>${markup}</body>`, "text/html");
  parsed.querySelectorAll("script, iframe, object, embed").forEach((node) => node.remove());
  parsed.body.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attribute) => {
      if (attribute.name.toLowerCase().startsWith("on")) node.removeAttribute(attribute.name);
      if (["href", "src"].includes(attribute.name.toLowerCase()) && !/^(#|\/)/.test(attribute.value)) node.removeAttribute(attribute.name);
    });
  });
  target.replaceChildren(...parsed.body.childNodes);
}

function money(value) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(Number(value || 0));
}

function closeCustomSelects(except = null) {
  document.querySelectorAll(".custom-select.open").forEach((wrapper) => {
    if (wrapper === except) return;
    wrapper.classList.remove("open", "open-up");
    const trigger = wrapper.querySelector("[data-custom-select-trigger]");
    const menu = wrapper.querySelector("[data-custom-select-menu]");
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (menu) menu.hidden = true;
  });
}

function syncCustomSelect(select) {
  if (!select) return;
  let wrapper = select.closest(".custom-select");
  if (!wrapper) {
    wrapper = document.createElement("div");
    wrapper.className = "custom-select";
    select.replaceWith(wrapper);
    wrapper.append(select);
  }

  let trigger = wrapper.querySelector("[data-custom-select-trigger]");
  let menu = wrapper.querySelector("[data-custom-select-menu]");
  if (!trigger) {
    trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "select-trigger";
    trigger.dataset.customSelectTrigger = "true";
    trigger.setAttribute("aria-haspopup", "listbox");
    wrapper.append(trigger);
  }
  if (!menu) {
    menu = document.createElement("div");
    menu.className = "select-menu";
    menu.dataset.customSelectMenu = "true";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    wrapper.append(menu);
  }

  if (!menu.id) menu.id = `custom-select-menu-${++customSelectSequence}`;
  if (!trigger.id) trigger.id = `custom-select-trigger-${customSelectSequence}`;
  trigger.setAttribute("aria-controls", menu.id);
  trigger.setAttribute("aria-expanded", wrapper.classList.contains("open") ? "true" : "false");
  trigger.disabled = select.disabled;
  trigger.setAttribute("aria-disabled", String(select.disabled));
  const explicitLabel = select.getAttribute("aria-label");
  if (explicitLabel) trigger.setAttribute("aria-label", explicitLabel);
  else trigger.removeAttribute("aria-label");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  const selected = select.selectedOptions[0] || select.options[0];
  trigger.textContent = selected?.textContent || "请选择";
  wrapper.classList.toggle("has-value", select.dataset.hasValue === "true");
  menu.replaceChildren(...[...select.options].map((option, index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "select-option";
    item.dataset.customSelectOption = "true";
    item.dataset.index = String(index);
    item.textContent = option.textContent;
    item.disabled = option.disabled;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(option === selected));
    return item;
  }));
}

function enhanceCustomSelects(root = document) {
  root.querySelectorAll("select").forEach(syncCustomSelect);
}

function openCustomSelect(wrapper) {
  const select = wrapper.querySelector("select");
  const trigger = wrapper.querySelector("[data-custom-select-trigger]");
  const menu = wrapper.querySelector("[data-custom-select-menu]");
  if (!select || !trigger || !menu || select.disabled) return;
  closeCustomSelects(wrapper);
  const rect = wrapper.getBoundingClientRect();
  wrapper.classList.toggle("open-up", rect.bottom + 250 > window.innerHeight && rect.top > 260);
  wrapper.classList.add("open");
  menu.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  const selected = menu.querySelector(`[data-index="${select.selectedIndex}"]`);
  menu.querySelectorAll(".select-option").forEach((item) => { item.tabIndex = -1; });
  (selected || menu.querySelector(".select-option:not(:disabled)"))?.focus();
}

function closeCustomSelect(wrapper, restoreFocus = false) {
  if (!wrapper) return;
  wrapper.classList.remove("open", "open-up");
  const trigger = wrapper.querySelector("[data-custom-select-trigger]");
  const menu = wrapper.querySelector("[data-custom-select-menu]");
  if (trigger) {
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  }
  if (menu) menu.hidden = true;
}

function chooseCustomOption(option) {
  const wrapper = option.closest(".custom-select");
  const select = wrapper?.querySelector("select");
  if (!wrapper || !select || option.disabled) return;
  select.value = select.options[Number(option.dataset.index)]?.value ?? "";
  closeCustomSelect(wrapper);
  select.dispatchEvent(new Event("change", { bubbles: true }));
  syncCustomSelect(select);
  wrapper.querySelector("[data-custom-select-trigger]")?.focus();
}

function parseLocalDate(value) {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function shortDate(value, withYear = false) {
  return new Intl.DateTimeFormat("zh-CN", {
    ...(withYear ? { year: "numeric" } : {}),
    month: "short",
    day: "numeric",
    weekday: "short",
  }).format(parseLocalDate(value));
}

function addDays(value, amount) {
  const result = parseLocalDate(value);
  result.setDate(result.getDate() + amount);
  return isoDate(result.getFullYear(), result.getMonth(), result.getDate());
}

function todoDueState(item) {
  if (!item.dueDate) return { key: "undated", label: "" };
  const days = Math.round((parseLocalDate(item.dueDate) - parseLocalDate(state.data.today)) / 86400000);
  if (days < 0) return { key: "overdue", label: `已逾期 ${Math.abs(days)} 天 · 截止 ${shortDate(item.dueDate)}` };
  if (days === 0) return { key: "today", label: "今天截止" };
  return { key: "upcoming", label: `截止 ${shortDate(item.dueDate)}` };
}

function monthLabel(month = state.month) {
  const [year, value] = month.split("-");
  return `${year}年 ${Number(value)}月`;
}

function icon(name) {
  return `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

async function loadData(showLoader = true) {
  if (state.loading) return;
  state.loading = true;
  if (showLoader) replaceHtml(viewRoot, `<div class="loading-state"><span class="loader"></span><p>正在整理你的生活数据…</p></div>`);
  try {
    state.data = await request(`/api/data?month=${encodeURIComponent(state.month)}`);
    state.month = state.data.month;
    if (!state.backup) loadBackupStatus(false);
    updateCategoryOptions();
    render();
    if (state.route === "organize") loadOrganizer();
  } catch (error) {
    replaceHtml(viewRoot, `<div class="empty-state"><strong>暂时无法读取数据</strong><p>${escapeHtml(error.message)}</p></div>`);
    toast("读取失败", error.message, true);
  } finally {
    state.loading = false;
  }
}

async function loadOrganizer() {
  if (state.organizerLoading) return;
  state.organizerLoading = true;
  state.organizer = null;
  state.organizerSuggestions = null;
  state.organizerReview = null;
  render();
  try {
    const month = state.organizerScope === "month" ? state.month : "";
    state.organizerMonth = month || state.organizerMonth;
    const query = new URLSearchParams({ scope: state.organizerScope });
    if (month) query.set("month", month);
    const organizer = await request(`/api/organize?${query.toString()}`);
    state.organizer = organizer;
    const transactionIds = new Set(organizer.transactions.map((item) => item.id));
    const diaryIds = new Set(organizer.diary.map((item) => item.id));
    const todoIds = new Set((organizer.todos || []).map((item) => item.id));
    for (const id of Object.keys(state.organizerDrafts.transactions)) if (!transactionIds.has(id)) delete state.organizerDrafts.transactions[id];
    for (const id of Object.keys(state.organizerDrafts.diary)) if (!diaryIds.has(id)) delete state.organizerDrafts.diary[id];
    for (const id of Object.keys(state.organizerDrafts.todos)) if (!todoIds.has(id)) delete state.organizerDrafts.todos[id];
    for (const id of Object.keys(state.organizerSelections.transactions)) if (!transactionIds.has(id)) delete state.organizerSelections.transactions[id];
    for (const id of Object.keys(state.organizerSelections.diary)) if (!diaryIds.has(id)) delete state.organizerSelections.diary[id];
    for (const id of Object.keys(state.organizerSelections.todos)) if (!todoIds.has(id)) delete state.organizerSelections.todos[id];
    organizer.transactions.forEach((item) => {
      if (!(item.id in state.organizerSelections.transactions)) state.organizerSelections.transactions[item.id] = true;
    });
    organizer.diary.forEach((item) => {
      if (!(item.id in state.organizerSelections.diary)) state.organizerSelections.diary[item.id] = true;
    });
    (organizer.todos || []).forEach((item) => {
      if (!(item.id in state.organizerSelections.todos)) state.organizerSelections.todos[item.id] = true;
    });
    render();
  } catch (error) {
    state.organizer = { transactions: [], diary: [], todos: [], categories: [], knownTags: [], error: error.message };
    render();
    toast("整理页读取失败", error.message, true);
  } finally {
    state.organizerLoading = false;
  }
}

async function loadBackupStatus(showError = true) {
  try {
    state.backup = await request("/api/backup/status");
    renderBackupStatus();
  } catch (error) {
    state.backup = { state: "error", lastError: error.message };
    renderBackupStatus();
    if (showError) toast("无法读取备份状态", error.message, true);
  }
}

async function loadSettings() {
  try {
    state.settings = await request("/api/settings");
    applyTheme(state.settings.application?.theme);
    if (!state.settings.application?.onboardingCompleted) showOnboarding();
    if (state.route === "settings") render();
  } catch (error) {
    if (state.route === "settings") toast("无法读取设置", error.message, true);
  }
}

function showOnboarding() {
  if (state.onboardingOpen) return;
  state.onboardingOpen = true;
  const backdrop = document.querySelector("#onboarding-backdrop");
  const path = document.querySelector("#onboarding-data-path");
  if (path) path.textContent = state.settings?.dataPath || "设置页中可以再次查看";
  if (backdrop) backdrop.hidden = false;
}

async function completeOnboarding() {
  const button = document.querySelector("[data-onboarding-complete]");
  const budget = document.querySelector("#onboarding-budget")?.value.trim() || "";
  button.disabled = true;
  try {
    state.settings = await request("/api/onboarding/complete", { method: "POST", body: JSON.stringify({ monthlyBudget: budget }) });
    state.onboardingOpen = false;
    document.querySelector("#onboarding-backdrop").hidden = true;
    if (budget) await loadData(false);
    toast("准备完成", "可以开始记录了。首次录入后建议在设置中备份。", false, 6000);
  } catch (error) {
    toast("还不能开始", error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderBackupStatus() {
  const status = state.backup;
  if (!status) return;
  const dot = document.querySelector("#backup-state-dot");
  const title = document.querySelector("#backup-state-title");
  const detail = document.querySelector("#backup-state-detail");
  const button = document.querySelector("#backup-button");
  const hasError = Boolean(status.lastError) || status.state === "error";
  const pending = Boolean(status.pending);
  const maintenance = status.maintenance || {};
  dot.className = `state-dot${hasError ? " error" : pending ? " pending" : ""}`;
  button.className = `backup-button${hasError ? " error" : pending ? " pending" : ""}`;
  button.disabled = Boolean(status.busy);
  button.querySelector("span").textContent = status.busy ? "处理中" : pending ? "等待备份" : "已备份";
  title.textContent = hasError ? "后台任务需要处理" : maintenance.busy ? "正在整理本地文件" : pending ? "本地已保存" : "数据已备份";
  if (hasError) {
    detail.textContent = status.lastError;
  } else if (maintenance.busy || maintenance.pending) {
    detail.textContent = maintenance.busy ? "正在生成可读文件和仪表盘" : `${maintenance.pending} 项等待后台整理`;
  } else if (pending) {
    const parts = [];
    if (status.changedFiles) parts.push(`${status.changedFiles} 个文件待提交`);
    if (status.commitsAhead) parts.push(`${status.commitsAhead} 个提交待推送`);
    detail.textContent = parts.join("，") || "有数据等待备份";
  } else {
    detail.textContent = status.lastBackupAt
      ? `上次备份 ${new Date(status.lastBackupAt).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
      : `当前方式：${{ local: "本机文件", webdav: "WebDAV", s3: "S3" }[status.backend] || "尚未配置"}`;
  }
  const updated = state.data ? new Date(state.data.generatedAt) : new Date();
  document.querySelector("#sidebar-updated").textContent = `${updated.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 已刷新`;
}

async function backupNow() {
  const button = document.querySelector("#backup-button");
  button.disabled = true;
  button.querySelector("span").textContent = "正在备份";
  try {
    const result = await request("/api/backup", { method: "POST", body: JSON.stringify({}) });
    state.backup = result.status;
    toast("备份完成", result.message);
  } catch (error) {
    toast("备份未完成", `${error.message} 本地数据不会丢失，可以稍后重试。`, true, 7000);
    await loadBackupStatus(false);
  } finally {
    button.disabled = false;
    renderBackupStatus();
  }
}

function updateCategoryOptions(parentValue = null, childValue = null) {
  const parent = document.querySelector('#form-transaction [name="categoryParent"]');
  const child = document.querySelector('#form-transaction [name="categoryChild"]');
  const childField = document.querySelector("#category-child-field");
  if (!parent || !child || !state.data) return;
  const selectedParent = parentValue === null ? parent.value : parentValue;
  const selectedChild = childValue === null ? child.value : childValue;
  parent.replaceChildren(
    new Option("未分类", ""),
    ...state.data.categoryTree.map((item) => new Option(item.name, item.name)),
    new Option("＋ 新增一级分类…", "__new_parent__"),
  );
  parent.value = state.data.categoryTree.some((item) => item.name === selectedParent) ? selectedParent : "";
  parent.dataset.lastValue = parent.value;
  parent.dataset.hasValue = parent.value ? "true" : "false";
  const treeItem = state.data.categoryTree.find((item) => item.name === parent.value);
  child.replaceChildren(
    new Option(treeItem ? "不使用二级分类" : "先选择一级分类", ""),
    ...(treeItem ? treeItem.children.map((name) => new Option(name, name)) : []),
    ...(treeItem ? [new Option("＋ 新增二级分类…", "__new_child__")] : []),
  );
  child.disabled = !treeItem;
  if (childField) childField.hidden = !treeItem;
  child.value = treeItem && treeItem.children.includes(selectedChild) ? selectedChild : "";
  child.dataset.lastValue = child.value;
  child.dataset.hasValue = child.value ? "true" : "false";
  const childNote = document.querySelector("#category-child-note");
  if (childNote) childNote.textContent = treeItem ? "可选；只选择一级分类也可以" : "先选择一级分类后可选二级分类";
  syncCustomSelect(parent);
  syncCustomSelect(child);
}

function closeQuickCategory() {
  state.categoryQuickAdd = null;
  const panel = document.querySelector("#category-quick-add");
  const input = document.querySelector("#category-quick-add-name");
  if (panel) panel.hidden = true;
  if (input) input.value = "";
}

function openQuickCategory(level, previousValue = "") {
  const parent = document.querySelector('#form-transaction [name="categoryParent"]');
  const panel = document.querySelector("#category-quick-add");
  const title = document.querySelector("#category-quick-add-title");
  const input = document.querySelector("#category-quick-add-name");
  if (!panel || !title || !input) return;
  if (level === "child" && !parent?.value) return;
  state.categoryQuickAdd = { level, previousValue };
  title.textContent = level === "parent" ? "新增一级分类" : `在“${parent.value}”下新增二级分类`;
  input.placeholder = level === "parent" ? "例如：健康" : "例如：医疗";
  input.value = "";
  panel.hidden = false;
  input.focus();
}

function handleCategoryParentChange(select) {
  if (select.value === "__new_parent__") {
    const previous = select.dataset.lastValue || "";
    select.value = previous;
    updateCategoryOptions(previous, "");
    openQuickCategory("parent", previous);
    return;
  }
  closeQuickCategory();
  updateCategoryOptions(select.value, "");
}

function handleCategoryChildChange(select) {
  if (select.value === "__new_child__") {
    const previous = select.dataset.lastValue || "";
    select.value = previous;
    updateCategoryOptions(null, previous);
    openQuickCategory("child", previous);
    return;
  }
  closeQuickCategory();
  select.dataset.lastValue = select.value;
}

async function quickAddCategory() {
  const formState = state.categoryQuickAdd;
  const input = document.querySelector("#category-quick-add-name");
  const button = document.querySelector("[data-category-quick-save]");
  const parent = document.querySelector('#form-transaction [name="categoryParent"]')?.value || "";
  const name = String(input?.value || "").trim().replaceAll(":", "").replaceAll(" · ", ":");
  if (!formState || !name || (formState.level === "child" && !parent)) {
    toast("还不能增加分类", formState?.level === "child" ? "请先选择一级分类。" : "请填写分类名称。", true);
    return;
  }
  const account = formState.level === "parent" ? name : `${parent}:${name}`;
  if (button) button.disabled = true;
  try {
    const result = await request("/api/categories", { method: "POST", body: JSON.stringify({ name: account }) });
    const selectedParent = formState.level === "parent" ? name : parent;
    const selectedChild = formState.level === "child" ? name : "";
    closeQuickCategory();
    await loadData(false);
    updateCategoryOptions(selectedParent, selectedChild);
    toast("分类已增加", result.message);
  } catch (error) {
    toast("没有增加分类", error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function monthControl() {
  return `<div class="month-control"><button data-month-step="-1" aria-label="上个月">${icon("chevron-left")}</button><span>${monthLabel()}</span><button data-month-step="1" aria-label="下个月">${icon("chevron-right")}</button></div>`;
}

function statCard(label, value, foot, accent, iconName) {
  return `<article class="stat-card accent-${accent}"><div class="stat-top"><span>${label}</span><span class="stat-icon">${icon(iconName)}</span></div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></article>`;
}

function categoryRows(limit = 6, detail = false) {
  const rows = (detail ? state.data.categoryDetails : state.data.categories).slice(0, limit);
  if (!rows.length) return `<div class="empty-state" style="min-height:180px"><p>这个月还没有支出</p></div>`;
  const colors = ["#d85f48", "#3c6d9c", "#b47719", "#7157a4", "#4c8a68", "#9b6a52"];
  const max = Math.max(...rows.map((item) => item.amount), 1);
  return `<div class="category-list">${rows.map((item, index) => `<div class="category-row" style="--dot:${colors[index % colors.length]}"><div class="category-name"><span class="category-dot"></span><span>${escapeHtml(item.label || item.name)}</span></div><div class="category-amount">${money(item.amount)}</div><div class="category-meter"><span style="width:${Math.max(4, item.amount / max * 100)}%"></span></div></div>`).join("")}</div>`;
}

function categoryPie(detail = false) {
  const rows = detail ? state.data.categoryDetails : state.data.categories;
  const total = rows.reduce((sum, item) => sum + item.amount, 0);
  if (!rows.length || total <= 0) return "";
  const colors = ["#d85f48", "#3c6d9c", "#b47719", "#7157a4", "#4c8a68", "#9b6a52", "#4f8f9d", "#a85d7b"];
  let cursor = 0;
  const slices = rows.map((item, index) => {
    const start = cursor;
    cursor += item.amount / total * 100;
    return `${colors[index % colors.length]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  });
  return `<div class="pie-wrap"><div class="pie-chart" role="img" aria-label="消费分类饼图" style="background:conic-gradient(${slices.join(",")})"><div class="pie-center"><strong>${money(total)}</strong><span>已分类支出</span></div></div></div>`;
}

function spendingChart() {
  const [year, month] = state.month.split("-").map(Number);
  const days = new Date(year, month, 0).getDate();
  const amountByDay = new Map(state.data.dailySpending.map((item) => [Number(item.date.slice(-2)), item.amount]));
  const max = Math.max(...amountByDay.values(), 1);
  return `<div class="spending-chart">${Array.from({ length: days }, (_, index) => {
    const day = index + 1;
    const amount = amountByDay.get(day) || 0;
    const height = amount ? Math.max(4, amount / max * 100) : 0;
    const showLabel = day === 1 || day === days || day % 5 === 0;
    return `<div class="chart-column" title="${day}日 · ${money(amount)}"><div class="chart-bar-wrap"><span class="chart-bar" style="height:${height}%"></span></div><span class="chart-label">${showLabel ? day : ""}</span></div>`;
  }).join("")}</div><div class="spending-legend"><span>每日支出</span><strong>${money(state.data.summary.monthSpend)}</strong></div>`;
}

function todoRows(items = state.data.todos, limit = null, options = {}) {
  const visible = limit ? items.slice(0, limit) : items;
  if (!visible.length) return `<div class="empty-state${options.compact ? " compact" : ""}"><p>${escapeHtml(options.empty || "待办已经全部完成了")}</p></div>`;
  return visible.map((item) => {
    const due = todoDueState(item);
    const meta = due.label || item.tags.length
      ? `<div class="todo-meta">${due.label ? `<small class="todo-due ${due.key}">${escapeHtml(due.label)}</small>` : ""}${item.tags.map((tag) => `<span class="tag amber">${escapeHtml(tag)}</span>`).join("")}</div>`
      : "";
    const reschedule = options.reschedule && due.key === "overdue"
      ? `<div class="todo-quick-actions"><button data-reschedule-todo="${escapeHtml(item.id)}" data-due-date="${state.data.today}">改到今天</button><button data-reschedule-todo="${escapeHtml(item.id)}" data-due-date="${addDays(state.data.today, 1)}">改到明天</button><button data-choose-todo-date="${escapeHtml(item.id)}">选择日期</button></div>`
      : "";
    const checkbox = options.selectable ? `<label class="table-check"><input type="checkbox" data-bulk-check data-bulk-kind="todo" data-bulk-id="${escapeHtml(item.id)}" ${state.bulkSelections.todo[item.id] ? "checked" : ""}/><span></span></label>` : "";
    return `<div class="todo-item ${due.key}${options.selectable ? " selectable-todo" : ""}">${checkbox}<button class="todo-check" data-complete-todo="${escapeHtml(item.id)}" aria-label="完成待办">${icon("tick")}</button><div class="todo-content"><p>${escapeHtml(item.text)}</p>${meta}${reschedule}</div><div class="row-actions"><button data-edit-item="todo" data-item-id="${escapeHtml(item.id)}">编辑</button><button class="danger" data-delete-item="todo" data-item-id="${escapeHtml(item.id)}">删除</button></div></div>`;
  }).join("");
}

function completedTodoRows(limit = 10, options = {}) {
  const items = (options.items || state.data.completedTodos).slice(0, limit);
  if (!items.length) return `<div class="empty-state compact"><p>近期还没有已完成待办</p></div>`;
  return items.map((item) => { const checkbox = options.selectable ? `<label class="table-check"><input type="checkbox" data-bulk-check data-bulk-kind="todo" data-bulk-id="${escapeHtml(item.id)}" ${state.bulkSelections.todo[item.id] ? "checked" : ""}/><span></span></label>` : ""; return `<div class="todo-item completed${options.selectable ? " selectable-todo" : ""}">${checkbox}<button class="todo-check checked" data-restore-todo="${escapeHtml(item.id)}" aria-label="恢复待办">${icon("tick")}</button><div class="todo-content"><p>${escapeHtml(item.text)}</p><div class="todo-meta"><small>${item.completedDate ? `${shortDate(item.completedDate)}完成` : "已完成"}</small>${item.tags.map((tag) => `<span class="tag amber">${escapeHtml(tag)}</span>`).join("")}</div></div><div class="row-actions"><button data-restore-todo="${escapeHtml(item.id)}">恢复</button><button class="danger" data-delete-item="todo" data-item-id="${escapeHtml(item.id)}">删除</button></div></div>`; }).join("");
}

function diaryRows(items = state.data.diary, limit = 3) {
  const visible = limit ? items.slice(0, limit) : items;
  if (!visible.length) return `<div class="empty-state" style="min-height:180px"><p>还没有日记</p></div>`;
  return visible.map((item) => `<article class="diary-entry"><div class="diary-meta"><time>${shortDate(item.date)}</time>${item.tags[0] ? `<span class="tag">${escapeHtml(item.tags[0])}</span>` : ""}</div><p>${escapeHtml(item.text)}</p></article>`).join("");
}

function renderOverview() {
  const hour = new Date().getHours();
  const greeting = hour < 11 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const summary = state.data.summary;
  const remaining = Math.max(summary.budget - (summary.budgetSpend ?? summary.monthSpend), 0);
  return `<div class="welcome-row"><div class="welcome-copy"><p>${shortDate(state.data.today, true)}</p><h2>${greeting}，今天也把生活<br><em>轻轻放在这里。</em></h2></div>${monthControl()}</div>
    <div class="stats-grid">
      ${statCard("本月支出", money(summary.monthSpend), `预算已使用 ${summary.budgetPercent}%`, "coral", "wallet")}
      ${statCard("剩余预算", money(remaining), `月度预算 ${money(summary.budget)}`, "green", "grid")}
      ${statCard("进行中待办", summary.todoCount, summary.todoCount ? "慢慢来，但别忘记" : "今天没有未完成事项", "amber", "check")}
      ${statCard("本月记录", `${summary.diaryDays} 天`, `近 7 天支出 ${money(summary.weekSpend)}`, "violet", "book")}
    </div>
    <div class="dashboard-grid">
      <div class="stack">
        <section class="card spending-card"><div class="card-head"><div class="card-title"><p>${monthLabel()}</p><h3>支出走势</h3></div><button class="text-button" data-route-link="expenses">查看明细 ${icon("arrow")}</button></div>${spendingChart()}</section>
        <section class="card"><div class="card-head"><div class="card-title"><p>生活轨迹</p><h3>本月日历</h3></div><button class="text-button" data-route-link="calendar">打开日历 ${icon("arrow")}</button></div>${calendarGrid(true)}</section>
      </div>
      <div class="stack">
        <section class="card"><div class="card-head"><div class="card-title"><p>支出结构</p><h3>消费分类</h3></div></div>${categoryRows(5)}</section>
        <section class="card"><div class="card-head"><div class="card-title"><p>${summary.todoCount} 项进行中</p><h3>接下来要做</h3></div><button class="text-button" data-open-composer="todo">添加 ${icon("plus")}</button></div><div class="todo-list">${todoRows(state.data.todos, 4)}</div></section>
        <section class="card"><div class="card-head"><div class="card-title"><p>最近片段</p><h3>日记</h3></div><button class="text-button" data-route-link="diary">全部 ${icon("arrow")}</button></div><div class="diary-list">${diaryRows()}</div></section>
      </div>
    </div>`;
}

function searchMatches() {
  const query = state.searchQuery.trim().toLowerCase();
  if (!query) return [];
  const matches = [];
  const add = (kind, item, title, body, date, readOnly = false) => {
    const haystack = [title, body, date, ...(item.tags || [])].join(" ").toLowerCase();
    if (haystack.includes(query)) matches.push({ kind, item, title, body, date, readOnly });
  };
  state.data.transactions.forEach((item) => add("transaction", item, item.summary, `${item.category} ${item.note}`, item.date));
  state.data.diary.forEach((item) => add("diary", item, item.text.slice(0, 80), item.text, item.date));
  [...state.data.todos, ...state.data.completedTodos].forEach((item) => add("todo", item, item.text, item.completed ? "已完成" : "进行中", item.date));
  state.data.events.forEach((item) => add("event", item, item.title, `${item.location} ${item.description}`, item.date, item.readOnly));
  return matches;
}

function renderSearch() {
  const query = state.searchQuery.trim();
  if (!query) return `<div class="empty-state"><strong>搜索你的记录</strong><p>在上方输入关键词，可以查找账目、日记、待办和日程。</p></div>`;
  const matches = searchMatches();
  const kindLabel = { transaction: "账目", diary: "日记", todo: "待办", event: "日程" };
  return `<div class="view-header"><div><h2>搜索结果</h2><p>“${escapeHtml(query)}”共找到 ${matches.length} 条记录</p></div></div><section class="card search-results">${matches.length ? matches.map((match) => `<div class="search-result"><div class="search-result-type">${kindLabel[match.kind]}</div><div class="search-result-copy"><strong>${escapeHtml(match.title)}</strong><small>${escapeHtml(match.date)} · ${escapeHtml(match.body)}</small></div>${match.readOnly ? `<span class="tag">只读</span>` : `<button class="text-button" data-edit-item="${match.kind}" data-item-id="${escapeHtml(match.item.id)}">打开</button>`}</div>`).join("") : `<div class="empty-state"><p>没有找到匹配记录</p></div>`}</section>`;
}

function itemsForDay(dateValue) {
  const items = [];
  state.data.events.filter((item) => item.date === dateValue).forEach((item) => items.push({ type: item.source === "subscription" ? "subscription" : "event", label: item.title }));
  const spending = state.data.transactions.filter((item) => item.date === dateValue).reduce((sum, item) => sum + item.amount, 0);
  if (spending) items.push({ type: "expense", label: `支出 ${money(spending)}` });
  const diaryCount = state.data.diary.filter((item) => item.date === dateValue).length;
  if (diaryCount) items.push({ type: "diary", label: `${diaryCount} 篇日记` });
  state.data.todos.filter((item) => item.dueDate === dateValue).slice(0, 1).forEach((item) => items.push({ type: "todo", label: item.text }));
  return items;
}

function dayDetailSection(title, tone, items, emptyText, renderItem) {
  return `<section class="day-detail-section ${tone}"><div class="day-detail-section-head"><h3>${title}</h3><span>${items.length} 项</span></div>${items.length ? `<div class="day-detail-records">${items.map(renderItem).join("")}</div>` : `<p class="day-detail-empty">${emptyText}</p>`}</section>`;
}

function dayDetailRecord(kind, item, title, meta, body = "", extra = "") {
  return `<button class="day-detail-record" type="button" data-day-detail-record data-day-detail-kind="${kind}" data-day-detail-id="${escapeHtml(item.id)}" ${extra}><span class="day-detail-record-main"><strong>${escapeHtml(title)}</strong>${body ? `<small>${escapeHtml(body)}</small>` : ""}</span><span class="day-detail-record-meta">${meta}</span></button>`;
}

function renderDayDetails() {
  if (!dayDetailPanel || !state.data || !state.dayDetailsDate) return;
  const date = state.dayDetailsDate;
  const title = document.querySelector("#day-detail-title");
  const body = document.querySelector("#day-detail-body");
  if (title) title.textContent = shortDate(date, true);
  if (!body) return;
  const diary = state.data.diary.filter((item) => item.date === date);
  const transactions = state.data.transactions.filter((item) => item.date === date);
  const events = state.data.events.filter((item) => item.date === date);
  const todos = state.data.todos.filter((item) => item.dueDate === date);
  const eventMeta = (item) => item.allDay ? "全天" : `${item.start.slice(11, 16)}${item.end ? `–${item.end.slice(11, 16)}` : ""}`;
  body.innerHTML = [
    dayDetailSection("日记", "violet", diary, "这一天还没有日记", (item) => dayDetailRecord("diary", item, item.text.slice(0, 80), item.tags.length ? item.tags.map((tag) => `#${tag}`).join(" ") : "打开查看全文")),
    dayDetailSection("支出", "coral", transactions, "这一天还没有支出", (item) => dayDetailRecord("transaction", item, item.summary, money(item.amount), item.category.replaceAll(":", " · "))),
    dayDetailSection("日程", "blue", events, "这一天还没有日程", (item) => dayDetailRecord("event", item, item.title, item.readOnly ? "订阅 · 只读" : eventMeta(item), item.location || item.description.slice(0, 80), item.readOnly ? "data-day-readonly=\"true\"" : "")),
    dayDetailSection("待办", "amber", todos, "这一天没有截止待办", (item) => dayDetailRecord("todo", item, item.text, "截止", item.tags.length ? item.tags.map((tag) => `#${tag}`).join(" ") : "")),
    `<div class="day-detail-actions"><p>只在明确点击新增按钮时创建新记录</p><div><button class="text-button" type="button" data-day-add="diary">${icon("book")}新增日记</button><button class="text-button" type="button" data-day-add="transaction">${icon("wallet")}新增支出</button><button class="text-button" type="button" data-day-add="todo">${icon("check")}新增待办</button><button class="text-button" type="button" data-day-add="event">${icon("calendar")}新增日程</button></div></div>`,
  ].join("");
}

function openDayDetails(dateValue) {
  state.dayDetailsDate = dateValue;
  renderDayDetails();
  backdrop.hidden = false;
  dayDetailPanel.classList.add("open");
  dayDetailPanel.setAttribute("aria-hidden", "false");
}

function closeDayDetails() {
  state.dayDetailsDate = null;
  dayDetailPanel.classList.remove("open");
  dayDetailPanel.setAttribute("aria-hidden", "true");
  if (!composer.classList.contains("open")) backdrop.hidden = true;
}

function isoDate(year, monthIndex, day) {
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function calendarGrid(compact = false) {
  const [year, month] = state.month.split("-").map(Number);
  const first = new Date(year, month - 1, 1);
  const gridStart = new Date(year, month - 1, 1 - first.getDay());
  const cells = Array.from({ length: 42 }, (_, index) => {
    const current = new Date(gridStart);
    current.setDate(gridStart.getDate() + index);
    const dateValue = isoDate(current.getFullYear(), current.getMonth(), current.getDate());
    const outside = current.getMonth() !== month - 1;
    const today = dateValue === state.data.today;
    const dayItems = itemsForDay(dateValue);
    const limit = compact ? 2 : 3;
    return `<div class="calendar-day${outside ? " outside" : ""}${today ? " today" : ""}" role="button" tabindex="0" aria-label="打开 ${shortDate(dateValue, true)} 详情" data-calendar-date="${dateValue}"><span class="day-number">${current.getDate()}</span><div class="day-items">${dayItems.slice(0, limit).map((item) => `<span class="day-event ${item.type}">${escapeHtml(item.label)}</span>`).join("")}${dayItems.length > limit ? `<span class="more-items">+${dayItems.length - limit}</span>` : ""}</div></div>`;
  }).join("");
  return `<div class="calendar-grid">${["日", "一", "二", "三", "四", "五", "六"].map((day) => `<div class="weekday">${day}</div>`).join("")}${cells}</div>`;
}

function upcomingEvents(limit = 8) {
  const today = state.data.today;
  return state.data.events.filter((item) => item.date >= today).slice(0, limit);
}

function eventRows(items = upcomingEvents()) {
  if (!items.length) return `<div class="empty-state" style="min-height:180px"><p>近期没有日程</p></div>`;
  return items.map((item) => {
    const date = parseLocalDate(item.date);
    const timing = item.allDay ? "全天" : item.start.slice(11, 16) + (item.end ? `–${item.end.slice(11, 16)}` : "");
    const actions = item.readOnly ? `<span class="tag">订阅</span>` : `<div class="row-actions"><button data-edit-item="event" data-item-id="${escapeHtml(item.id)}">编辑</button><button class="danger" data-delete-item="event" data-item-id="${escapeHtml(item.id)}">删除</button></div>`;
    return `<div class="event-row"><div class="event-date"><strong>${date.getDate()}</strong><small>${date.getMonth() + 1}月</small></div><div class="event-copy"><strong>${escapeHtml(item.title)}</strong><span>${timing}${item.location ? ` · ${escapeHtml(item.location)}` : ""}</span></div>${actions}</div>`;
  }).join("");
}

function renderCalendar() {
  return `<div class="view-header"><div><h2>${monthLabel()}日历</h2><p>日程、支出、日记与待办在日期上相遇</p></div>${monthControl()}</div><div class="data-layout"><section class="card calendar-card">${calendarGrid()}</section><aside class="card"><div class="card-head"><div class="card-title"><p>未来安排</p><h3>近期日程</h3></div><button class="text-button" data-open-composer="event">添加 ${icon("plus")}</button></div><div class="event-list">${eventRows()}</div></aside></div>`;
}

function categoryAccountOptions(selected = "") {
  const options = [`<option value="">选择分类</option>`];
  for (const item of state.data.categoryTree || []) {
    options.push(`<option value="expenses:${escapeHtml(item.name)}" ${selected === `expenses:${item.name}` ? "selected" : ""}>${escapeHtml(item.name.replaceAll(":", " · "))}</option>`);
    for (const child of item.children || []) {
      const account = `expenses:${child}`;
      options.push(`<option value="${escapeHtml(account)}" ${selected === account ? "selected" : ""}>${escapeHtml(child.replaceAll(":", " · "))}</option>`);
    }
  }
  return options.join("");
}

function filteredTransactions() {
  const category = state.listFilters.expenseCategory;
  return state.data.transactions.filter((item) => item.date.startsWith(state.month) && (!category || item.category === category));
}

function filteredDiary() {
  const tag = state.listFilters.diaryTag.trim().toLowerCase();
  return state.data.diary.filter((item) => !tag || item.tags.some((value) => value.toLowerCase().includes(tag)) || item.text.toLowerCase().includes(tag));
}

function filteredTodos() {
  const status = state.listFilters.todoStatus;
  const active = status === "completed" ? [] : state.data.todos;
  const completed = status === "active" ? [] : state.data.completedTodos;
  return { active, completed };
}

function bulkToolbar(kind, items) {
  const selected = items.filter((item) => state.bulkSelections[kind][item.id]);
  const count = selected.length;
  const control = kind === "transaction"
    ? `<select data-bulk-draft="transaction" aria-label="批量分类">${categoryAccountOptions(state.bulkDrafts.transaction)}</select>`
    : `<input data-bulk-draft="${kind}" value="${escapeHtml(state.bulkDrafts[kind])}" placeholder="输入标签，用空格分隔" maxlength="120" />`;
  return `<div class="bulk-toolbar"><span>已选择 ${count} 项</span><div class="bulk-controls">${control}<button class="text-button" type="button" data-bulk-preview="${kind}" ${count ? "" : "disabled"}>预览修改</button><button class="text-button" type="button" data-bulk-clear="${kind}" ${count ? "" : "disabled"}>清除选择</button></div></div>`;
}

function bulkPreviewMarkup() {
  const preview = state.bulkPreview;
  if (!preview) return "";
  const labels = preview.items.map((item) => item.summary || item.text || item.title).slice(0, 8);
  const more = preview.items.length > labels.length ? `等 ${preview.items.length} 项` : "";
  const value = preview.kind === "transaction" ? preview.value.replace(/^expenses:/, "").replaceAll(":", " · ") : (preview.value || "无标签");
  return `<div class="bulk-preview"><div><strong>确认批量修改 ${preview.items.length} 项？</strong><p>${labels.map((label) => escapeHtml(label)).join("、")}${more}</p><small>将${preview.kind === "transaction" ? "分类为" : "标签设为"}：${escapeHtml(value)}</small></div><div class="inline-actions"><button class="submit-button dark" type="button" data-bulk-confirm>确认应用</button><button class="text-button" type="button" data-bulk-cancel>取消</button></div></div>`;
}

function openBulkPreview(kind) {
  const collections = { transaction: filteredTransactions(), diary: filteredDiary(), todo: [...filteredTodos().active, ...filteredTodos().completed] };
  const items = collections[kind].filter((item) => state.bulkSelections[kind][item.id]);
  const value = state.bulkDrafts[kind].trim();
  if (!items.length) return toast("还没有选择记录", "请先勾选需要批量编辑的记录。", true);
  if (!value) return toast("还没有填写修改内容", kind === "transaction" ? "请选择目标分类。" : "请输入至少一个标签。", true);
  state.bulkPreview = { kind, items, ids: items.map((item) => item.id), value };
  render();
}

async function applyBulkEdit() {
  const preview = state.bulkPreview;
  if (!preview) return;
  const payload = { transactions: [], diary: [], todos: [] };
  if (preview.kind === "transaction") payload.transactions = preview.ids.map((id) => ({ id, account: preview.value }));
  if (preview.kind === "diary") payload.diary = preview.ids.map((id) => ({ id, tags: tagsFrom(preview.value) }));
  if (preview.kind === "todo") payload.todos = preview.ids.map((id) => ({ id, tags: tagsFrom(preview.value) }));
  try {
    const result = await request("/api/bulk-edit", { method: "POST", body: JSON.stringify(payload) });
    state.bulkSelections[preview.kind] = {};
    state.bulkDrafts[preview.kind] = "";
    state.bulkPreview = null;
    toast("批量修改完成", result.message);
    await loadData(false);
  } catch (error) {
    toast("批量修改没有保存", error.message, true, 7000);
  }
}

function clearBulkSelection(kind) {
  state.bulkSelections[kind] = {};
  state.bulkPreview = null;
  render();
}

function renderExpenses() {
  const selected = filteredTransactions();
  const categories = [...new Set(state.data.transactions.map((item) => item.category))].sort();
  const rows = selected.map((item) => `<tr><td><label class="table-check"><input type="checkbox" data-bulk-check data-bulk-kind="transaction" data-bulk-id="${escapeHtml(item.id)}" ${state.bulkSelections.transaction[item.id] ? "checked" : ""}/><span></span></label></td><td><span class="transaction-title">${escapeHtml(item.summary)}</span>${item.note ? `<br><small class="muted">${escapeHtml(item.note)}</small>` : ""}</td><td>${shortDate(item.date)}</td><td><span class="tag coral">${escapeHtml(item.category.replaceAll(":", " · "))}</span>${item.budget_excluded ? ` <span class="tag amber">预算外</span>` : ""}</td><td class="money">${money(item.amount)}</td><td><div class="row-actions"><button data-edit-item="transaction" data-item-id="${escapeHtml(item.id)}">编辑</button><button class="danger" data-delete-item="transaction" data-item-id="${escapeHtml(item.id)}">删除</button></div></td></tr>`).join("") || `<tr><td colspan="6" class="muted">没有符合条件的消费记录</td></tr>`;
  return `<div class="view-header"><div><h2>消费记录</h2><p>${monthLabel()}共 ${selected.length} 笔支出</p></div><div style="display:flex;gap:10px">${monthControl()}<button class="primary-button" data-open-composer="transaction">${icon("plus")}记一笔</button></div></div><div class="list-filters"><label>分类<select data-list-filter="expenseCategory"><option value="">全部分类</option>${categories.map((item) => `<option value="${escapeHtml(item)}" ${state.listFilters.expenseCategory === item ? "selected" : ""}>${escapeHtml(item.replaceAll(":", " · "))}</option>`).join("")}</select></label></div>${bulkToolbar("transaction", selected)}${bulkPreviewMarkup()}<div class="data-layout"><section class="card"><table class="data-table"><thead><tr><th></th><th>项目</th><th>日期</th><th>分类</th><th style="text-align:right">金额</th><th></th></tr></thead><tbody>${rows}</tbody></table></section><aside class="card"><div class="card-head"><div class="card-title"><p>${money(state.data.summary.monthSpend)}</p><h3>分类占比</h3></div><div class="segmented"><button class="${state.categoryView === "parent" ? "active" : ""}" data-category-view="parent">一级</button><button class="${state.categoryView === "detail" ? "active" : ""}" data-category-view="detail">明细</button></div></div>${categoryPie(state.categoryView === "detail")}${categoryRows(12, state.categoryView === "detail")}</aside></div>`;
}

function renderDiary() {
  const selected = filteredDiary();
  const rows = selected.map((item) => `<article class="card diary-page-entry"><header><label class="table-check"><input type="checkbox" data-bulk-check data-bulk-kind="diary" data-bulk-id="${escapeHtml(item.id)}" ${state.bulkSelections.diary[item.id] ? "checked" : ""}/><span></span></label><time>${shortDate(item.date, true)}</time><div class="entry-head-actions"><div>${item.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ")}</div><div class="row-actions"><button data-edit-item="diary" data-item-id="${escapeHtml(item.id)}">编辑</button><button class="danger" data-delete-item="diary" data-item-id="${escapeHtml(item.id)}">删除</button></div></div></header><p>${escapeHtml(item.text)}</p></article>`).join("") || `<div class="empty-state"><p>没有符合条件的日记</p></div>`;
  return `<div class="view-header"><div><h2>日记</h2><p>符合条件的生活片段，共 ${selected.length} 篇</p></div><button class="primary-button" data-open-composer="diary">${icon("plus")}写日记</button></div><div class="list-filters"><label>搜索日记或标签<input data-list-filter="diaryTag" value="${escapeHtml(state.listFilters.diaryTag)}" placeholder="例如：旅行、工作" /></label></div>${bulkToolbar("diary", selected)}${bulkPreviewMarkup()}<div class="diary-page-list">${rows}</div>`;
}

function organizerCategoryOptions(selected = "") {
  const options = [`<option value="">保持未分类</option>`];
  for (const category of state.organizer?.categories || []) {
    const account = `expenses:${category}`;
    options.push(`<option value="${escapeHtml(account)}" ${selected === account ? "selected" : ""}>${escapeHtml(category.replaceAll(":", " · "))}</option>`);
  }
  return options.join("");
}

function organizerTransactionAccount(item) {
  const draft = state.organizerDrafts.transactions[item.id];
  if (draft !== undefined) return draft;
  return state.organizerSuggestions?.transactions?.find((suggestion) => suggestion.id === item.id)?.account || item.account || "";
}

function organizerDiaryTags(item) {
  const draft = state.organizerDrafts.diary[item.id];
  if (draft !== undefined) return draft;
  const suggestion = state.organizerSuggestions?.diary?.find((entry) => entry.id === item.id);
  return suggestion ? suggestion.tags.join(" ") : (item.tags || []).join(" ");
}

function organizerTodoTags(item) {
  const draft = state.organizerDrafts.todos?.[item.id];
  if (draft !== undefined) return draft;
  const suggestion = state.organizerSuggestions?.todos?.find((entry) => entry.id === item.id);
  return suggestion ? suggestion.tags.join(" ") : (item.tags || []).join(" ");
}

function organizerTagsChanged(item, value) {
  return JSON.stringify(tagsFrom(value)) !== JSON.stringify(item.tags || []);
}

function organizerReviewMode() {
  return state.organizer?.scope && state.organizer.scope !== "unorganized";
}

function organizerScopeLabel() {
  if (state.organizerScope === "month") return `${monthLabel(state.organizerMonth || state.month)}记录`;
  if (state.organizerScope === "all") return "全部记录";
  return "待整理记录";
}

function organizerSuggestionCount() {
  return (state.organizerSuggestions?.transactions?.length || 0) + (state.organizerSuggestions?.diary?.length || 0) + (state.organizerSuggestions?.todos?.length || 0);
}

function organizerApplyCount() {
  if (!state.organizer) return 0;
  if (state.organizerReview && state.organizerReview.status !== "completed") return 0;
  const transactions = state.organizer.transactions.filter((item) => state.organizerSelections.transactions[item.id] && organizerTransactionAccount(item) !== item.account);
  const diary = state.organizer.diary.filter((item) => state.organizerSelections.diary[item.id] && organizerTagsChanged(item, organizerDiaryTags(item)));
  const todos = (state.organizer.todos || []).filter((item) => state.organizerSelections.todos[item.id] && organizerTagsChanged(item, organizerTodoTags(item)));
  return transactions.length + diary.length + todos.length;
}

function organizerReviewStatusText(review) {
  if (!review) return "";
  const total = review.totalBatches || 0;
  const completed = review.completedBatches || 0;
  if (review.status === "failed") return `已完成 ${completed}/${total} 批，部分批次失败`;
  if (review.status === "completed") return `已完成 ${completed}/${total} 批`;
  return `正在复核第 ${Math.min(completed + 1, total)}/${total} 批`;
}

function organizerHistoryRows() {
  const history = state.organizer?.history || [];
  if (!history.length) return `<p class="muted">还没有应用过 AI 复核修改。</p>`;
  const value = (item) => Array.isArray(item) ? (item.length ? item.join("、") : "无标签") : item;
  return history.slice(0, 12).map((item) => `<div class="organizer-history-row"><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.date)} · ${escapeHtml(item.field)}</small></div><span>${escapeHtml(value(item.before))} → ${escapeHtml(value(item.after))}</span><time>${escapeHtml(String(item.appliedAt || "").replace("T", " ").slice(0, 16))}</time></div>`).join("");
}

function syncOrganizerApplyButton() {
  const button = document.querySelector("[data-organizer-apply]");
  if (!button) return;
  const count = organizerApplyCount();
  button.disabled = !count;
  button.textContent = count ? `应用 ${count} 项修改` : "修改";
}

function organizerTransactionRows() {
  const items = state.organizer?.transactions || [];
  if (!items.length) return `<div class="organizer-empty"><span>${icon("tick")}</span><div><strong>${organizerReviewMode() ? "这个范围没有账目" : "账目已经分好类了"}</strong><p>${organizerReviewMode() ? "换个范围，或稍后再来看看。" : "没有待整理的未分类支出。"}</p></div></div>`;
  return items.map((item) => {
    const account = organizerTransactionAccount(item);
    const suggested = state.organizerSuggestions?.transactions?.some((entry) => entry.id === item.id);
    const label = suggested ? "AI 建议" : (organizerReviewMode() ? "当前分类" : "分类");
    return `<div class="organizer-row"><label class="organizer-check"><input type="checkbox" data-organizer-check="transaction" data-organizer-id="${escapeHtml(item.id)}" ${state.organizerSelections.transactions[item.id] ? "checked" : ""}/><span></span></label><div class="organizer-copy"><strong>${escapeHtml(item.summary)}</strong><small>${shortDate(item.date)} · ${money(item.amount)}${item.note ? ` · ${escapeHtml(item.note)}` : ""}</small></div><div class="organizer-control ${suggested ? "suggested" : ""}"><span class="organizer-control-label">${label}</span><select data-organizer-select="transaction" data-organizer-id="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.summary)} 的分类">${organizerCategoryOptions(account)}</select></div></div>`;
  }).join("");
}

function organizerDiaryRows() {
  const items = state.organizer?.diary || [];
  if (!items.length) return `<div class="organizer-empty"><span>${icon("tick")}</span><div><strong>${organizerReviewMode() ? "这个范围没有日记" : "日记都已经有标签了"}</strong><p>${organizerReviewMode() ? "换个范围，或稍后再来看看。" : "没有待整理的无标签日记。"}</p></div></div>`;
  return items.map((item) => {
    const tags = organizerDiaryTags(item);
    const suggested = state.organizerSuggestions?.diary?.some((entry) => entry.id === item.id);
    return `<div class="organizer-row diary-organizer-row"><label class="organizer-check"><input type="checkbox" data-organizer-check="diary" data-organizer-id="${escapeHtml(item.id)}" ${state.organizerSelections.diary[item.id] ? "checked" : ""}/><span></span></label><div class="organizer-copy"><strong>${shortDate(item.date, true)}</strong><p>${escapeHtml(item.text)}</p></div><label class="organizer-control tag-control ${suggested ? "suggested" : ""}"><span class="organizer-control-label">${suggested ? "AI 建议" : (organizerReviewMode() ? "当前标签" : "标签")}</span><input type="text" data-organizer-tags data-organizer-id="${escapeHtml(item.id)}" value="${escapeHtml(tags)}" placeholder="例如：生活 工作" maxlength="120" /></label></div>`;
  }).join("");
}

function organizerTodoRows() {
  const items = state.organizer?.todos || [];
  if (!items.length) return `<div class="organizer-empty"><span>${icon("tick")}</span><div><strong>${organizerReviewMode() ? "这个范围没有待办" : "待办都已经有标签了"}</strong><p>${organizerReviewMode() ? "换个范围，或稍后再来看看。" : "没有待整理的无标签待办。"}</p></div></div>`;
  return items.map((item) => {
    const tags = organizerTodoTags(item);
    const suggested = state.organizerSuggestions?.todos?.some((entry) => entry.id === item.id);
    const status = item.completed ? " · 已完成" : "";
    return `<div class="organizer-row todo-organizer-row"><label class="organizer-check"><input type="checkbox" data-organizer-check="todo" data-organizer-id="${escapeHtml(item.id)}" ${state.organizerSelections.todos[item.id] ? "checked" : ""}/><span></span></label><div class="organizer-copy"><strong>${escapeHtml(item.text)}</strong><small>${shortDate(item.date)}${item.dueDate ? ` · 截止 ${shortDate(item.dueDate)}` : ""}${status}</small></div><label class="organizer-control tag-control ${suggested ? "suggested" : ""}"><span class="organizer-control-label">${suggested ? "AI 建议" : (organizerReviewMode() ? "当前标签" : "标签")}</span><input type="text" data-organizer-tags data-organizer-kind="todo" data-organizer-id="${escapeHtml(item.id)}" value="${escapeHtml(tags)}" placeholder="例如：工作 生活" maxlength="120" /></label></div>`;
  }).join("");
}

function renderOrganizer() {
  if (!state.organizer) return `<div class="loading-state"><span class="loader"></span><p>正在查找需要整理的记录…</p></div>`;
  if (state.organizer.error) return `<div class="empty-state"><strong>暂时无法读取整理列表</strong><p>${escapeHtml(state.organizer.error)}</p></div>`;
  const total = state.organizer.transactions.length + state.organizer.diary.length + (state.organizer.todos || []).length;
  const suggestions = organizerSuggestionCount();
  const applyCount = organizerApplyCount();
  const review = state.organizerReview;
  const reviewRunning = review && ["pending", "running"].includes(review.status);
  const reviewAction = review?.status === "failed"
    ? `<button class="primary-button" type="button" data-organizer-retry>${icon("refresh")}重试失败批次</button>`
    : `<button class="primary-button" type="button" data-organizer-suggest ${total && !reviewRunning ? "" : "disabled"}>${icon("spark")}${reviewRunning ? "AI 正在复核…" : "AI 生成建议"}</button>`;
  const reviewText = organizerReviewMode() ? "复核范围内的已有分类和标签，AI 只提供建议，不会自动写入。" : "把暂时没有分类或标签的内容集中处理，AI 只提供建议，不会自动写入。";
  const title = organizerReviewMode() ? `整理记录 · ${organizerScopeLabel()}` : "整理记录";
  const transactionTitle = organizerReviewMode() ? "范围内账目" : "未分类账目";
  const diaryTitle = organizerReviewMode() ? "范围内日记" : "无标签日记";
  const todoTitle = organizerReviewMode() ? "范围内待办" : "无标签待办";
  const progressNotice = review ? `<div class="organizer-progress ${review.status === "failed" ? "failed" : ""}"><span class="state-dot ${review.status === "failed" ? "error" : "pending"}"></span><p><strong>${review.status === "failed" ? "AI 复核遇到问题" : "AI 历史复核"}</strong><small>${organizerReviewStatusText(review)}${review.lastError ? ` · ${escapeHtml(review.lastError)}` : ""}</small></p>${review.status === "failed" ? `<button class="text-button" type="button" data-organizer-retry>重试</button>` : ""}</div>` : "";
  return `<div class="view-header"><div><h2>${title}</h2><p>${reviewText}</p></div><div class="organizer-actions"><div class="organizer-scopes" role="group" aria-label="整理范围"><button class="${state.organizerScope === "unorganized" ? "active" : ""}" type="button" data-organizer-scope="unorganized">待整理</button><button class="${state.organizerScope === "month" ? "active" : ""}" type="button" data-organizer-scope="month">最近一个月</button><button class="${state.organizerScope === "all" ? "active" : ""}" type="button" data-organizer-scope="all">全部记录</button></div><button class="text-button" type="button" data-organizer-refresh>${icon("refresh")}刷新</button>${reviewAction}<button class="submit-button dark organizer-apply" type="button" data-organizer-apply ${applyCount ? "" : "disabled"}>应用 ${applyCount ? `${applyCount} 项修改` : "修改"}</button></div></div>${progressNotice}${suggestions ? `<div class="organizer-notice"><span>${icon("spark")}</span><p>AI 已提出 ${suggestions} 项建议，请逐项确认或修改后，再点击“应用”。</p><button class="text-button" type="button" data-organizer-clear>清除建议</button></div>` : ""}<div class="organizer-summary"><div><strong>${state.organizer.transactions.length}</strong><span>${organizerReviewMode() ? "笔账目" : "笔未分类账目"}</span></div><div><strong>${state.organizer.diary.length}</strong><span>${organizerReviewMode() ? "篇日记" : "篇无标签日记"}</span></div><div><strong>${(state.organizer.todos || []).length}</strong><span>${organizerReviewMode() ? "项待办" : "项无标签待办"}</span></div><p>勾选要处理的记录；留空表示暂不修改。</p></div><div class="organizer-grid"><section class="card organizer-card"><div class="card-head"><div class="card-title"><p>消费</p><h3>${transactionTitle}</h3></div><span class="section-count">${state.organizer.transactions.length} 笔</span></div><div class="organizer-list">${organizerTransactionRows()}</div></section><section class="card organizer-card"><div class="card-head"><div class="card-title"><p>生活片段</p><h3>${diaryTitle}</h3></div><span class="section-count">${state.organizer.diary.length} 篇</span></div><div class="organizer-list">${organizerDiaryRows()}</div></section><section class="card organizer-card"><div class="card-head"><div class="card-title"><p>行动</p><h3>${todoTitle}</h3></div><span class="section-count">${(state.organizer.todos || []).length} 项</span></div><div class="organizer-list">${organizerTodoRows()}</div></section></div><section class="card organizer-history"><div class="card-head"><div class="card-title"><p>可追溯记录</p><h3>最近应用的修改</h3></div></div><div class="organizer-history-list">${organizerHistoryRows()}</div></section>`;
}

function renderTodos() {
  const { active, completed } = filteredTodos();
  const groups = { overdue: [], today: [], upcoming: [], undated: [] };
  active.forEach((item) => groups[todoDueState(item).key].push(item));
  const section = (key, eyebrow, title, empty) => groups[key].length ? `<section class="card todo-section ${key}"><div class="card-head"><div class="card-title"><p>${eyebrow}</p><h3>${title}</h3></div><span class="section-count">${groups[key].length} 项</span></div><div class="todo-page-list">${todoRows(groups[key], null, { reschedule: key === "overdue", empty, selectable: true })}</div></section>` : "";
  return `<div class="view-header"><div><h2>待办清单</h2><p>${active.length} 项进行中，${completed.length} 项已完成</p></div><button class="primary-button" data-open-composer="todo">${icon("plus")}添加待办</button></div><div class="list-filters"><label>显示<select data-list-filter="todoStatus"><option value="active" ${state.listFilters.todoStatus === "active" ? "selected" : ""}>进行中</option><option value="completed" ${state.listFilters.todoStatus === "completed" ? "selected" : ""}>已完成</option><option value="all" ${state.listFilters.todoStatus === "all" ? "selected" : ""}>全部</option></select></label></div>${bulkToolbar("todo", [...active, ...completed])}${bulkPreviewMarkup()}<div class="todo-sections">${section("overdue", "需要处理", "已逾期", "没有逾期待办")}${section("today", "今天", "今天截止", "没有今天截止任务")}${section("upcoming", "接下来", "即将截止", "近期没有截止任务")}${section("undated", "随时可做", "无截止日期", "没有无截止日期")}<section class="card completed-card"><div class="card-head"><div class="card-title"><p>最近完成</p><h3>可以撤销误操作</h3></div></div><div class="todo-page-list">${completedTodoRows(50, { items: completed, selectable: true })}</div></section></div>`;
}

function renderSettingsBase() {
  if (!state.settings) return `<div class="loading-state"><span class="loader"></span><p>正在读取本机设置…</p></div>`;
  const ai = state.settings.ai;
  const backup = state.settings.backup;
  const finance = state.settings.finance;
  const categoryTreeRows = state.data.categoryTree.map((item) => {
    const hasChildren = item.children.length > 0;
    const expanded = state.categoryExpanded[item.name] !== false;
    const toggleLabel = expanded ? `收起 ${item.name}` : `展开 ${item.name}`;
    const childRows = item.children.map((child) => `<div class="category-tree-row child"><div><span>${escapeHtml(child)}</span><small>二级分类</small></div><button type="button" class="danger-link" data-delete-category="${escapeHtml(`${item.name}:${child}`)}">删除</button></div>`).join("");
    const childEditor = `<form class="category-child-form" data-category-level="child"><input type="hidden" name="categoryParent" value="${escapeHtml(item.name)}" /><label>新增二级分类<input name="categoryChild" maxlength="40" placeholder="例如：医疗" required /></label><button class="text-button" type="submit">增加二级分类 ${icon("plus")}</button></form>`;
    return `<div class="category-tree-group"><div class="category-tree-row primary"><button type="button" class="category-toggle" data-category-toggle="${escapeHtml(item.name)}" aria-expanded="${expanded}" aria-label="${escapeHtml(toggleLabel)}">${icon("chevron-right")}<span><strong>${escapeHtml(item.name)}</strong><small>${hasChildren ? `${item.children.length} 个二级分类` : "暂无二级分类"}</small></span></button><button type="button" class="danger-link" data-delete-category="${escapeHtml(item.name)}">删除</button></div><div class="category-tree-children"${expanded ? "" : " hidden"}>${childRows || `<p class="category-tree-empty">还没有二级分类</p>`}${childEditor}</div></div>`;
  }).join("");
  const webdav = backup.webdav;
  const s3 = backup.s3;
  const proxy = backup.proxy || { mode: "system", url: "", username: "", passwordConfigured: false };
  const application = state.settings.application || {};
  const subscriptions = state.settings.calendarSubscriptions || [];
  const subscriptionRows = subscriptions.map((item) => {
    const status = item.cached ? `${item.count} 项 · ${new Date(item.updatedAt).toLocaleDateString("zh-CN")}` : "尚未缓存";
    return `<div class="subscription-row"><label class="subscription-toggle"><input type="checkbox" aria-label="显示 ${escapeHtml(item.name)}" data-toggle-subscription="${escapeHtml(item.id)}" ${item.enabled ? "checked" : ""}/><span></span></label><div class="subscription-copy"><strong>${escapeHtml(item.name)}</strong><small title="${escapeHtml(item.url)}">${escapeHtml(item.url)}</small><em>${escapeHtml(status)}</em></div><div class="row-actions"><button type="button" data-refresh-subscription="${escapeHtml(item.id)}">更新</button><button type="button" class="danger" data-delete-subscription="${escapeHtml(item.id)}">删除</button></div></div>`;
  }).join("");
  const subscriptionEditor = state.subscriptionEditorOpen ? `<div class="subscription-editor"><label>名称<input id="new-subscription-name" maxlength="80" placeholder="例如：公司日历" /></label><label>ICS 地址<input id="new-subscription-url" type="url" placeholder="https://example.com/calendar.ics" /></label><div class="inline-actions"><button class="submit-button dark" type="button" data-confirm-subscription>添加并更新</button><button class="text-button" type="button" data-cancel-subscription>取消</button></div></div>` : "";
  return `<div class="view-header"><div><h2>设置</h2><p>管理 AI、日历订阅、备份和数据迁移</p></div></div><div class="settings-grid">
    <div class="settings-form"><form id="settings-form" class="settings-form">
      <section class="card settings-card"><div class="card-head"><div class="card-title"><p>外观</p><h3>主题</h3></div></div><label>界面主题<select name="theme"><option value="system" ${application.theme === "system" ? "selected" : ""}>跟随系统</option><option value="light" ${application.theme === "light" ? "selected" : ""}>浅色</option><option value="dark" ${application.theme === "dark" ? "selected" : ""}>深色</option></select></label><p class="settings-note">选择会保存在本机；“跟随系统”会根据操作系统的明暗设置自动切换。</p></section>
      <section class="card settings-card"><div class="card-head"><div class="card-title"><p>AI 录入</p><h3>模型服务</h3></div></div><label class="check-field"><input name="aiEnabled" type="checkbox" ${ai.enabled ? "checked" : ""}/><span>启用 AI 录入</span></label><label>服务类型<select name="provider"><option value="deepseek" ${ai.provider === "deepseek" ? "selected" : ""}>DeepSeek</option><option value="openai-compatible" ${ai.provider === "openai-compatible" ? "selected" : ""}>OpenAI 兼容接口</option></select></label><label>API 地址<input name="baseUrl" value="${escapeHtml(ai.baseUrl)}" required /></label><label>模型<input name="model" value="${escapeHtml(ai.model)}" required /></label><label>API Key<input name="apiKey" type="password" placeholder="${ai.apiKeyConfigured ? "已配置；留空表示不修改" : "请输入 API Key"}" autocomplete="off" /></label><small>API Key 只保存在本机；备份会默认携带本机密钥。</small><div class="inline-actions connection-actions"><button class="text-button" type="button" data-test-ai>测试连接</button><small id="ai-test-status"></small></div></section>
      <section class="card settings-card"><div class="card-head"><div class="card-title"><p>消费</p><h3>月度预算</h3></div></div><label>每月预算<input name="monthlyBudget" type="number" min="0" max="999999999.99" step="0.01" value="${finance.monthlyBudget}" required /></label><p class="settings-note">标记为“不计入月度预算”的支出仍会进入消费总额和分类统计，但不会占用预算。</p></section>
      <section class="card settings-card subscription-card"><div class="card-head"><div class="card-title"><p>日历</p><h3>订阅日历</h3></div><button class="text-button" type="button" data-add-subscription>${icon("plus")}添加订阅</button></div><div class="subscription-list">${subscriptionRows || `<p class="muted">还没有订阅日历</p>`}</div>${subscriptionEditor}<p class="settings-note">取消勾选只会隐藏该订阅；删除会同时清除本机缓存。</p></section>
      <section class="card settings-card"><div class="card-head"><div class="card-title"><p>数据保护</p><h3>备份与恢复</h3></div></div><label>备份方式<select name="backupBackend" id="backup-backend"><option value="local" ${backup.backend === "local" ? "selected" : ""}>本机备份文件</option><option value="webdav" ${backup.backend === "webdav" ? "selected" : ""}>WebDAV</option><option value="s3" ${backup.backend === "s3" ? "selected" : ""}>S3 兼容存储</option></select></label><div class="backend-fields" data-backend-fields="webdav" ${backup.backend === "webdav" ? "" : "hidden"}><label>WebDAV 文件夹地址<input name="webdavUrl" value="${escapeHtml(webdav.url)}" placeholder="https://example.com/dav/daily-log" /></label><label>用户名<input name="webdavUsername" value="${escapeHtml(webdav.username)}" autocomplete="username" /></label><label>密码<input name="webdavPassword" type="password" placeholder="${webdav.passwordConfigured ? "已配置；留空不修改" : "请输入密码"}" autocomplete="new-password" /></label></div><div class="backend-fields" data-backend-fields="s3" ${backup.backend === "s3" ? "" : "hidden"}><label>S3 端点<input name="s3Endpoint" value="${escapeHtml(s3.endpoint)}" placeholder="https://s3.amazonaws.com" /></label><div class="field-row"><label>区域<input name="s3Region" value="${escapeHtml(s3.region)}" /></label><label>存储桶<input name="s3Bucket" value="${escapeHtml(s3.bucket)}" /></label></div><label>目录前缀<input name="s3Prefix" value="${escapeHtml(s3.prefix)}" /></label><label>Access Key<input name="s3AccessKey" type="password" placeholder="${s3.accessKeyConfigured ? "已配置；留空不修改" : "请输入 Access Key"}" /></label><label>Secret Key<input name="s3SecretKey" type="password" placeholder="${s3.secretKeyConfigured ? "已配置；留空不修改" : "请输入 Secret Key"}" /></label></div><div class="proxy-settings"><div class="card-head"><div class="card-title"><p>高级网络</p><h3>远程备份代理</h3></div></div><label>代理方式<select name="backupProxyMode" id="backup-proxy-mode"><option value="system" ${proxy.mode === "system" ? "selected" : ""}>跟随系统/环境代理</option><option value="none" ${proxy.mode === "none" ? "selected" : ""}>不使用代理</option><option value="custom" ${proxy.mode === "custom" ? "selected" : ""}>使用自定义代理</option></select></label><div id="backup-proxy-fields" ${proxy.mode === "custom" ? "" : "hidden"}><label>代理地址<input name="backupProxyUrl" value="${escapeHtml(proxy.url)}" placeholder="http://127.0.0.1:7890" /></label><label>代理用户名（可选）<input name="backupProxyUsername" value="${escapeHtml(proxy.username || "")}" autocomplete="username" /></label><label>代理密码（可选）<input name="backupProxyPassword" type="password" placeholder="${proxy.passwordConfigured ? "已配置；留空不修改" : "没有密码则留空"}" autocomplete="new-password" /></label></div><p class="settings-note">系统/环境代理使用操作系统和环境变量的设置；自定义代理仅用于 WebDAV/S3。代理地址不要填写用户名和密码。</p></div><label class="check-field"><input name="includeData" type="checkbox" ${backup.includeData ? "checked" : ""}/><span>备份日程、账目、日记、待办和普通设置</span></label><label class="check-field"><input name="encryptBackup" type="checkbox" ${backup.encryptBackup ? "checked" : ""}/><span>使用密码加密整个备份</span></label><label>备份密码<input name="encryptionPassword" type="password" placeholder="${backup.encryptionPasswordConfigured ? "已设置；留空表示不修改" : "加密整个备份时必填，至少 8 个字符"}" autocomplete="new-password" /></label><p class="settings-note">备份会默认携带 API Key、远程存储密钥和代理凭据；不勾选加密时，这些内容会以明文保存在备份文件中。</p><label class="check-field"><input name="autoBackup" type="checkbox" ${backup.autoBackup ? "checked" : ""}/><span>闲置后自动备份</span></label><label>闲置秒数<input name="idleSeconds" type="number" min="10" max="3600" value="${backup.idleSeconds}" /></label><div class="inline-actions"><button class="submit-button" type="button" data-restore-backup>从最新备份恢复</button><input class="compact-password" name="restorePassword" type="password" placeholder="如果备份已加密，在此输入密码" autocomplete="off" /></div><p class="settings-note">恢复会覆盖当前本地数据；开始前会自动留一份安全副本。</p><small>本机数据：${escapeHtml(state.settings.dataPath)}</small></section>
      <div class="settings-actions"><button class="submit-button dark" type="submit">保存设置</button></div>
    </form><section class="card settings-card category-settings-card"><div class="card-head"><div class="card-title"><p>消费</p><h3>消费分类</h3></div></div><div class="category-tree">${categoryTreeRows || `<p class="muted">还没有分类</p>`}</div><form id="category-add-form" class="category-rename-form"><label>新增一级分类<input name="categoryParent" list="category-parent-options" placeholder="例如：健康" required /></label><button class="submit-button dark" type="submit">增加一级分类</button></form><p class="settings-note">展开一级分类后，可以在分组内部增加二级分类。删除一级分类时会同时处理其二级分类；删除前会询问历史账目的迁移位置。</p></section></div>
    <details class="advanced-settings"><summary><span><small>高级设置</small><strong>数据目录与导出</strong></span><em>不常用的维护操作</em></summary><div class="settings-form"><section class="card settings-card data-location-card"><div class="card-head"><div class="card-title"><p>数据目录</p><h3>迁移数据位置</h3></div></div><label>当前目录<input value="${escapeHtml(state.settings.dataPath)}" readonly /></label><label>迁移到<input id="new-data-path" type="text" placeholder="例如：D:\\DailyLogData" /></label><p class="settings-note">迁移前会自动生成安全副本。目标目录必须为空；完成后请重启应用，原目录不会被删除。</p><button class="submit-button dark" type="button" data-relocate-data>迁移数据目录</button></section><section class="card settings-card"><div class="card-head"><div class="card-title"><p>数据迁移</p><h3>导出给其他软件</h3></div></div><label>导出格式<select id="export-format"><option value="expenses-csv">账目 CSV</option><option value="diary-markdown">日记 Markdown</option><option value="todo-txt">待办 todo.txt</option><option value="calendar-ics">日程 iCalendar（ICS）</option><option value="org">综合 Org Mode</option></select></label><p class="settings-note">每次只生成一种标准格式的文件；完整数据保护请使用上方的备份功能。</p><button class="submit-button dark" type="button" data-export-data>导出所选格式</button><p class="export-path" id="export-path"></p></section></div></details></div>`;
}

function renderSettings() {
  let markup = renderSettingsBase();
  markup = markup.replace(
    '<label class="check-field"><input name="includeData"',
    '<div class="inline-actions connection-actions"><button class="text-button" type="button" data-test-backup>测试连接</button><small id="backup-test-status"></small></div><label class="check-field"><input name="includeData"',
  );
  markup = markup.replace(
    /<\/details><\/div>$/,
    `<section class="card settings-card about-card"><div class="card-head"><div class="card-title"><p>关于</p><h3>${escapeHtml(state.settings.about?.name || "Daily Log")}</h3></div></div><p class="settings-note">版本 ${escapeHtml(state.settings.about?.version || "")} · MIT License</p><p class="settings-note">许可证文件和第三方依赖清单随安装包提供。</p></section></div></div>`,
  );
  return markup;
}

function render() {
  if (!state.data) return;
  const [title, eyebrow] = routeMeta[state.route];
  pageTitle.textContent = title;
  pageEyebrow.textContent = eyebrow;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.route === state.route));
  const renderers = { overview: renderOverview, calendar: renderCalendar, expenses: renderExpenses, diary: renderDiary, todos: renderTodos, organize: renderOrganizer, search: renderSearch, settings: renderSettings };
  replaceHtml(viewRoot, renderers[state.route]());
  enhanceCustomSelects(viewRoot);
  renderDayDetails();
}

function setRoute(route) {
  if (!routeMeta[route]) return;
  if (state.route !== route) state.bulkPreview = null;
  if (route !== "search") state.searchReturnRoute = route;
  state.route = route;
  sidebar.classList.remove("open");
  render();
  if (route === "organize") loadOrganizer();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setMonth(step) {
  const [year, month] = state.month.split("-").map(Number);
  const target = new Date(year, month - 1 + step, 1);
  state.month = `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, "0")}`;
  loadData(false);
}

function clearEditState(form) {
  delete form.dataset.itemId;
  const button = form.querySelector("button[type=submit]");
  if (button?.dataset.createText) button.textContent = button.dataset.createText;
}

function openComposer(form = "transaction", dateValue = null, editing = false) {
  selectForm(form);
  const targetForm = document.querySelector(`#form-${form}`);
  if (!editing && targetForm) {
    targetForm.reset();
    clearEditState(targetForm);
    if (form === "transaction") {
      closeQuickCategory();
      updateCategoryOptions("", "");
    }
    setDefaultDates();
  }
  if (dateValue) {
    const input = document.querySelector(`#form-${form} [name="date"]`);
    if (input) input.value = dateValue;
  }
  backdrop.hidden = false;
  composer.classList.add("open");
  composer.setAttribute("aria-hidden", "false");
  setTimeout(() => composer.querySelector(".entry-form.active input, .entry-form.active textarea")?.focus(), 180);
}

function findItem(kind, id) {
  const collections = { transaction: state.data.transactions, diary: state.data.diary, todo: [...state.data.todos, ...state.data.completedTodos], event: state.data.events };
  return collections[kind]?.find((item) => item.id === id);
}

function openEditor(kind, id) {
  const item = findItem(kind, id);
  if (!item) return toast("无法编辑", "记录已经变化，请刷新页面后重试。", true);
  openComposer(kind, null, true);
  const form = document.querySelector(`#form-${kind}`);
  form.reset();
  form.dataset.itemId = id;
  const set = (name, value) => {
    const input = form.elements.namedItem(name);
    if (!input) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  };
  if (kind === "transaction") {
    const parts = item.category === "未分类" ? [""] : item.category.split(":");
    const parent = parts.shift() || "";
    const child = parts.join(":");
    updateCategoryOptions(parent, child);
    set("date", item.date); set("summary", item.summary); set("amount", item.amount); set("note", item.note);
  } else if (kind === "diary") {
    set("date", item.date); set("text", item.text); set("tags", item.tags.join(" "));
  } else if (kind === "todo") {
    set("dueDate", item.dueDate); set("text", item.text); set("tags", item.tags.join(" "));
  } else if (kind === "event") {
    set("title", item.title); set("date", item.date); set("location", item.location); set("description", item.description);
    form.elements.namedItem("allDay").checked = item.allDay;
    set("startTime", item.allDay ? "" : item.start.slice(11, 16));
    set("endTime", item.allDay || !item.end ? "" : item.end.slice(11, 16));
    document.querySelector(".time-fields").hidden = item.allDay;
  }
  const button = form.querySelector("button[type=submit]");
  button.textContent = "保存修改";
}

function closeComposer() {
  composer.classList.remove("open");
  composer.setAttribute("aria-hidden", "true");
  if (!state.dayDetailsDate) setTimeout(() => { backdrop.hidden = true; }, 280);
}

function selectForm(form) {
  document.querySelectorAll(".composer-tab").forEach((button) => button.classList.toggle("active", button.dataset.form === form));
  document.querySelectorAll(".entry-form").forEach((entry) => entry.classList.toggle("active", entry.dataset.kind === form));
}

function tagsFrom(value) {
  return String(value || "").split(/[\s,，]+/).map((item) => item.replace(/^@/, "").trim()).filter(Boolean);
}

function makePlan(kind, values) {
  const plan = { journal: [], transactions: [], todos: [], calendar: [], clarifications: [] };
  if (kind === "transaction") {
    const category = [values.categoryParent, values.categoryChild].map((part) => String(part || "").trim().replaceAll(" · ", ":").replace(/^expenses:/, "")).filter(Boolean).join(":");
    const account = category ? `expenses:${category}` : "expenses";
    const editingId = document.querySelector("#form-transaction")?.dataset.itemId;
    const existing = editingId ? findItem("transaction", editingId) : null;
    plan.transactions.push({ date: values.date, summary: values.summary, amount: values.amount, account, note: values.note, budget_excluded: Boolean(existing?.budget_excluded) });
  } else if (kind === "diary") {
    plan.journal.push({ date: values.date, text: values.text, tags: tagsFrom(values.tags) });
  } else if (kind === "todo") {
    const editingId = document.querySelector("#form-todo")?.dataset.itemId;
    const existing = editingId ? findItem("todo", editingId) : null;
    plan.todos.push({ created_date: existing?.date || state.data.today, due_date: values.dueDate || null, text: values.text, tags: tagsFrom(values.tags) });
  } else if (kind === "event") {
    plan.calendar.push({ date: values.date, title: values.title, start_time: values.allDay ? "" : values.startTime, end_time: values.allDay ? "" : values.endTime, location: values.location, description: values.description });
  }
  return plan;
}

async function submitEntry(form) {
  const button = form.querySelector("button[type=submit]");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = form.dataset.kind === "ai" ? "AI 正在整理…" : "正在保存…";
  try {
    const values = Object.fromEntries(new FormData(form).entries());
    values.allDay = form.querySelector('[name="allDay"]')?.checked ?? false;
    if (form.dataset.kind === "ai") {
      const result = await request("/api/ai/record", { method: "POST", body: JSON.stringify({ text: values.text }) });
      toast("AI 已完成记录", result.summary || result.message, false, 7000);
      form.reset();
      closeComposer();
      await loadData(false);
      return;
    }
    const plan = makePlan(form.dataset.kind, values);
    const planKeys = { transaction: "transactions", diary: "journal", todo: "todos", event: "calendar" };
    const result = form.dataset.itemId
        ? await request(`/api/items/${form.dataset.kind}/${form.dataset.itemId}`, { method: "PUT", body: JSON.stringify({ item: plan[planKeys[form.dataset.kind]][0] }) })
        : await request("/api/record", { method: "POST", body: JSON.stringify({ plan }) });
    toast(form.dataset.itemId ? "修改成功" : "记录成功", result.message);
    form.reset();
    clearEditState(form);
    setDefaultDates();
    closeComposer();
    await loadData(false);
  } catch (error) {
    toast("没有写入", error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function completeTodo(id) {
  const todo = state.data.todos.find((item) => item.id === id);
  if (!todo) return;
  state.data.todos = state.data.todos.filter((item) => item.id !== id);
  state.data.completedTodos.unshift({ ...todo, completed: true, completedDate: state.data.today });
  render();
  try {
    const result = await request(`/api/todos/${id}/complete`, { method: "POST", body: JSON.stringify({}) });
    toast("待办已完成", result.message);
    await loadData(false);
  } catch (error) {
    toast("操作失败", error.message, true);
    await loadData(false);
  }
}

async function rescheduleTodo(id, dueDate) {
  const todo = state.data.todos.find((item) => item.id === id);
  if (!todo) return;
  const previous = todo.dueDate;
  todo.dueDate = dueDate;
  render();
  try {
    const result = await request(`/api/items/todo/${id}`, {
      method: "PUT",
      body: JSON.stringify({ item: { created_date: todo.date, due_date: dueDate, text: todo.text, tags: todo.tags } }),
    });
    toast("截止日期已修改", result.message);
    await loadData(false);
  } catch (error) {
    todo.dueDate = previous;
    render();
    toast("改期失败", error.message, true);
    await loadData(false);
  }
}

async function restoreTodo(id) {
  const todo = state.data.completedTodos.find((item) => item.id === id);
  if (!todo) return;
  state.data.completedTodos = state.data.completedTodos.filter((item) => item.id !== id);
  state.data.todos.unshift({ ...todo, completed: false, completedDate: null });
  render();
  try {
    const result = await request(`/api/todos/${id}/restore`, { method: "POST", body: JSON.stringify({}) });
    toast("待办已恢复", result.message);
    await loadData(false);
  } catch (error) {
    toast("恢复失败", error.message, true);
    await loadData(false);
  }
}

async function saveSettings(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    state.settings = await request("/api/settings", { method: "PUT", body: JSON.stringify({
      ai: { enabled: form.elements.aiEnabled.checked, provider: values.provider, baseUrl: values.baseUrl, model: values.model, apiKey: values.apiKey },
      finance: { monthlyBudget: values.monthlyBudget },
      application: { theme: values.theme },
      backup: {
        autoBackup: form.elements.autoBackup.checked,
        idleSeconds: Number(values.idleSeconds),
        backend: values.backupBackend,
        includeData: form.elements.includeData.checked,
        encryptBackup: form.elements.encryptBackup.checked,
        encryptionPassword: values.encryptionPassword,
        webdav: { url: values.webdavUrl, username: values.webdavUsername, password: values.webdavPassword, allowPrivate: form.elements.webdavAllowPrivate?.checked ?? state.settings.backup.webdav.allowPrivate },
        s3: { endpoint: values.s3Endpoint, region: values.s3Region, bucket: values.s3Bucket, prefix: values.s3Prefix, accessKey: values.s3AccessKey, secretKey: values.s3SecretKey, allowPrivate: form.elements.s3AllowPrivate?.checked ?? state.settings.backup.s3.allowPrivate },
        proxy: { mode: values.backupProxyMode, url: values.backupProxyUrl, username: values.backupProxyUsername, password: values.backupProxyPassword },
      },
    }) });
    applyTheme(state.settings.application?.theme);
    toast("设置已保存", "配置只保存在这台电脑。");
    render();
  } catch (error) {
    toast("设置未保存", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function relocateData(button) {
  const input = document.querySelector("#new-data-path");
  const path = input?.value.trim();
  if (!path) {
    toast("还不能迁移", "请填写新的数据目录。", true);
    return;
  }
  if (!window.confirm(`确定把本机数据复制到以下目录吗？\n\n${path}\n\n目标目录必须为空；完成后需要重启应用。`)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在迁移…";
  try {
    const result = await request("/api/settings/data-dir", { method: "POST", body: JSON.stringify({ path }) });
    toast("数据目录已迁移", `${result.message} 安全副本：${result.safetyBackup}`, false, 10000);
    input.value = "";
  } catch (error) {
    toast("数据目录未迁移", error.message, true, 8000);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function testAi(button) {
  const form = document.querySelector("#settings-form");
  const status = document.querySelector("#ai-test-status");
  button.disabled = true;
  if (status) status.textContent = "正在测试…";
  try {
    const result = await request("/api/settings/test-ai", { method: "POST", body: JSON.stringify({
      baseUrl: form.elements.baseUrl.value,
      model: form.elements.model.value,
      apiKey: form.elements.apiKey.value,
    }) });
    if (status) status.textContent = result.message;
    toast("AI 连接成功", result.message);
  } catch (error) {
    if (status) status.textContent = error.message;
    toast("AI 连接失败", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function testBackup(button) {
  const form = document.querySelector("#settings-form");
  const status = document.querySelector("#backup-test-status");
  const values = Object.fromEntries(new FormData(form).entries());
  button.disabled = true;
  if (status) status.textContent = "正在测试…";
  try {
    const result = await request("/api/settings/test-backup", { method: "POST", body: JSON.stringify({
      backend: values.backupBackend,
      webdav: { url: values.webdavUrl, username: values.webdavUsername, password: values.webdavPassword },
      s3: { endpoint: values.s3Endpoint, region: values.s3Region, bucket: values.s3Bucket, prefix: values.s3Prefix, accessKey: values.s3AccessKey, secretKey: values.s3SecretKey },
      proxy: { mode: values.backupProxyMode, url: values.backupProxyUrl, username: values.backupProxyUsername, password: values.backupProxyPassword },
    }) });
    if (status) status.textContent = result.message;
    toast("备份连接成功", result.message);
  } catch (error) {
    if (status) status.textContent = error.message;
    toast("备份连接失败", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function refreshSubscription(button, id) {
  button.disabled = true;
  try {
    const result = await request("/api/calendar/subscriptions/refresh", { method: "POST", body: JSON.stringify({ id }) });
    toast("订阅已更新", result.message);
    await Promise.all([loadSettings(), loadData(false)]);
  } catch (error) {
    toast("订阅更新失败", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function addSubscription() {
  const name = document.querySelector("#new-subscription-name")?.value.trim();
  const url = document.querySelector("#new-subscription-url")?.value.trim();
  const button = document.querySelector("[data-confirm-subscription]");
  if (!name || !url) {
    toast("还不能添加", "请填写订阅名称和 ICS 地址。", true);
    return;
  }
  button.disabled = true;
  try {
    const result = await request("/api/calendar/subscriptions", { method: "POST", body: JSON.stringify({ name, url }) });
    state.subscriptionEditorOpen = false;
    toast("订阅已添加", result.message);
    await Promise.all([loadSettings(), loadData(false)]);
  } catch (error) {
    toast("订阅没有添加", error.message, true);
    button.disabled = false;
  }
}

async function toggleSubscription(input) {
  input.disabled = true;
  try {
    const result = await request("/api/calendar/subscriptions/toggle", { method: "POST", body: JSON.stringify({ id: input.dataset.toggleSubscription, enabled: input.checked }) });
    toast(input.checked ? "订阅已显示" : "订阅已隐藏", result.message);
    await Promise.all([loadSettings(), loadData(false)]);
  } catch (error) {
    toast("订阅状态没有改变", error.message, true);
    await loadSettings();
  }
}

async function deleteSubscription(id) {
  const item = state.settings.calendarSubscriptions.find((entry) => entry.id === id);
  if (!item || !window.confirm(`确定删除订阅“${item.name}”吗？`)) return;
  try {
    const result = await request("/api/calendar/subscriptions/delete", { method: "POST", body: JSON.stringify({ id }) });
    toast("订阅已删除", result.message);
    await Promise.all([loadSettings(), loadData(false)]);
  } catch (error) {
    toast("订阅没有删除", error.message, true);
  }
}

async function restoreLatestBackup(button) {
  if (!window.confirm("确定从当前备份位置恢复最新备份吗？\n\n当前日程、账目、日记和待办会被覆盖，系统会先自动留一份安全副本。")) return;
  const form = document.querySelector("#settings-form");
  button.disabled = true;
  try {
    const result = await request("/api/backup/restore", { method: "POST", body: JSON.stringify({ password: form.elements.restorePassword.value }) });
    toast("恢复完成", `已从 ${result.source} 恢复；安全副本保存在 ${result.safetyBackup}`, false, 9000);
    await Promise.all([loadSettings(), loadData(false), loadBackupStatus(false)]);
  } catch (error) {
    toast("恢复失败", error.message, true, 7000);
  } finally {
    button.disabled = false;
  }
}

async function addCategory(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const parent = String(values.categoryParent || "").trim().replaceAll(" · ", ":").replace(/^expenses:/, "").replaceAll(":", "");
  const child = String(values.categoryChild || "").trim().replaceAll(" · ", ":").replace(/^expenses:/, "").replaceAll(":", "");
  const isChildForm = form.dataset.categoryLevel === "child";
  if (isChildForm && !parent) {
    toast("没有增加分类", "找不到所属的一级分类，请重新展开后再试。", true);
    return;
  }
  if (isChildForm && !child) {
    toast("没有增加分类", "请填写二级分类名称。", true);
    return;
  }
  const name = [parent, child].filter(Boolean).join(":");
  try {
    const result = await request("/api/categories", { method: "POST", body: JSON.stringify({ name }) });
    toast("分类已增加", result.message);
    if (isChildForm) state.categoryExpanded[parent] = true;
    form.reset();
    await loadData(false);
  } catch (error) {
    toast("没有增加分类", error.message, true);
  }
}

async function deleteCategory(name) {
  const target = window.prompt(`删除分类“${name.replaceAll(":", " · ")}”后，相关账目迁移到哪里？\n\n填写分类名称；留空则改为未分类。`, "");
  if (target === null) return;
  try {
    const result = await request("/api/categories/delete", { method: "POST", body: JSON.stringify({ name, migrateTo: target }) });
    toast("分类已删除", result.message);
    await loadData(false);
  } catch (error) {
    toast("分类没有删除", error.message, true);
  }
}

async function exportData(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在导出…";
  try {
    const format = document.querySelector("#export-format")?.value;
    const result = await request("/api/export", { method: "POST", body: JSON.stringify({ format }) });
    const path = document.querySelector("#export-path");
    if (path) path.textContent = `已保存到：${result.path}`;
    toast("导出完成", result.path, false, 7000);
  } catch (error) {
    toast("导出失败", error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function toggleBackupFields(value) {
  document.querySelectorAll("[data-backend-fields]").forEach((section) => {
    section.hidden = section.dataset.backendFields !== value;
  });
}

function toggleProxyFields(value) {
  const fields = document.querySelector("#backup-proxy-fields");
  if (fields) fields.hidden = value !== "custom";
}

async function deleteItem(kind, id) {
  const item = findItem(kind, id);
  if (!item) return;
  const labels = { transaction: item.summary, diary: item.text.slice(0, 30), todo: item.text, event: item.title };
  if (!window.confirm(`确定删除“${labels[kind]}”吗？此操作会立即修改本地数据。`)) return;
  try {
    const result = await request(`/api/items/${kind}/${id}`, { method: "DELETE" });
    toast("已删除", result.message);
    await loadData(false);
  } catch (error) {
    toast("删除失败", error.message, true);
  }
}

async function requestOrganizerSuggestions(button) {
  const transactionIds = Object.entries(state.organizerSelections.transactions).filter(([, selected]) => selected).map(([id]) => id);
  const diaryIds = Object.entries(state.organizerSelections.diary).filter(([, selected]) => selected).map(([id]) => id);
  const todoIds = Object.entries(state.organizerSelections.todos).filter(([, selected]) => selected).map(([id]) => id);
  if (!transactionIds.length && !diaryIds.length && !todoIds.length) {
    toast("还没有选择记录", "请先勾选需要让 AI 整理的账目、日记或待办。", true);
    return;
  }
  button.disabled = true;
  const original = button.innerHTML;
  button.textContent = "AI 正在分析…";
  try {
    state.organizerReview = await request("/api/organize/reviews", {
      method: "POST",
      body: JSON.stringify({ scope: state.organizerScope, month: state.organizerMonth || state.month, transactionIds, diaryIds, todoIds }),
    });
    state.organizerSuggestions = state.organizerReview.suggestions;
    render();
    await runOrganizerReview();
  } catch (error) {
    toast("AI 没有生成建议", error.message, true, 7000);
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function runOrganizerReview() {
  const review = state.organizerReview;
  if (!review || review.status === "completed" || review.status === "failed") return;
  while (state.organizerReview && ["pending", "running"].includes(state.organizerReview.status)) {
    state.organizerReview = await request(`/api/organize/reviews/${encodeURIComponent(review.id)}/next`, { method: "POST", body: "{}" });
    state.organizerSuggestions = state.organizerReview.suggestions;
    render();
  }
  if (state.organizerReview?.status === "completed") {
    toast("AI 建议已生成", "请确认分类和标签后，再应用修改。", false, 7000);
  } else if (state.organizerReview?.status === "failed") {
    toast("AI 历史复核未完成", "可以点击“重试”继续失败的批次，已完成的批次无需重复处理。", true, 8000);
  }
}

async function retryOrganizerReview() {
  if (!state.organizerReview?.id) return;
  try {
    state.organizerReview = await request(`/api/organize/reviews/${encodeURIComponent(state.organizerReview.id)}/retry`, { method: "POST", body: "{}" });
    state.organizerSuggestions = state.organizerReview.suggestions;
    render();
    await runOrganizerReview();
  } catch (error) {
    toast("重试失败", error.message, true, 7000);
  }
}

async function applyOrganizer() {
  const transactions = (state.organizer?.transactions || [])
    .filter((item) => state.organizerSelections.transactions[item.id] && organizerTransactionAccount(item) !== item.account)
    .map((item) => ({ id: item.id, account: organizerTransactionAccount(item) }));
  const diary = (state.organizer?.diary || [])
    .filter((item) => state.organizerSelections.diary[item.id] && organizerTagsChanged(item, organizerDiaryTags(item)))
    .map((item) => ({ id: item.id, tags: tagsFrom(organizerDiaryTags(item)) }));
  const todos = (state.organizer?.todos || [])
    .filter((item) => state.organizerSelections.todos[item.id] && organizerTagsChanged(item, organizerTodoTags(item)))
    .map((item) => ({ id: item.id, tags: tagsFrom(organizerTodoTags(item)) }));
  if (!transactions.length && !diary.length && !todos.length) {
    toast("没有需要应用的修改", "请先填写与当前不同的分类或标签。", true);
    return;
  }
  const button = document.querySelector("[data-organizer-apply]");
  if (button) {
    button.disabled = true;
    button.textContent = "正在保存…";
  }
  try {
    const result = await request("/api/organize/apply", { method: "POST", body: JSON.stringify({ scope: state.organizerScope, month: state.organizerMonth || state.month, reviewId: state.organizerReview?.id || "", transactions, diary, todos }) });
    toast("整理完成", result.message);
    await Promise.all([loadData(false), loadOrganizer()]);
  } catch (error) {
    toast("整理没有保存", error.message, true, 7000);
    if (button) button.disabled = false;
  }
}

function clearOrganizerSuggestions() {
  state.organizerSuggestions = null;
  render();
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function toast(title, message, error = false, timeout = 4200, link = null) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  const heading = document.createElement("strong");
  heading.textContent = String(title ?? "");
  const body = document.createElement("p");
  body.textContent = String(message ?? "");
  const safeUrl = link ? safeExternalUrl(link.url) : null;
  if (safeUrl) {
    body.append(document.createTextNode(" · "));
    const anchor = document.createElement("a");
    anchor.href = safeUrl;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.textContent = String(link.label || "打开链接");
    body.append(anchor);
  }
  element.append(heading, body);
  document.querySelector("#toast-region").append(element);
  setTimeout(() => element.remove(), timeout);
}

function setDefaultDates() {
  const now = new Date();
  const today = isoDate(now.getFullYear(), now.getMonth(), now.getDate());
  document.querySelectorAll('.entry-form input[type="date"]:not([name="dueDate"])').forEach((input) => { if (!input.value) input.value = today; });
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-custom-select-trigger]");
  if (trigger) {
    event.preventDefault();
    const wrapper = trigger.closest(".custom-select");
    if (wrapper?.classList.contains("open")) closeCustomSelect(wrapper);
    else openCustomSelect(wrapper);
    return;
  }
  const option = event.target.closest("[data-custom-select-option]");
  if (option) {
    event.preventDefault();
    chooseCustomOption(option);
    return;
  }
  if (!event.target.closest(".custom-select")) closeCustomSelects();
});

document.addEventListener("click", (event) => {
  const routeButton = event.target.closest("[data-route]");
  if (routeButton) setRoute(routeButton.dataset.route);
  const routeLink = event.target.closest("[data-route-link]");
  if (routeLink) setRoute(routeLink.dataset.routeLink);
  const opener = event.target.closest("[data-open-composer]");
  if (opener) openComposer(opener.dataset.openComposer);
  const tab = event.target.closest("[data-form]");
  if (tab) selectForm(tab.dataset.form);
  const monthButton = event.target.closest("[data-month-step]");
  if (monthButton) setMonth(Number(monthButton.dataset.monthStep));
  const day = event.target.closest("[data-calendar-date]");
  if (day) openDayDetails(day.dataset.calendarDate);
  const dayAdd = event.target.closest("[data-day-add]");
  if (dayAdd) {
    const date = state.dayDetailsDate;
    closeDayDetails();
    openComposer(dayAdd.dataset.dayAdd, date);
  }
  const dayRecord = event.target.closest("[data-day-detail-record]");
  if (dayRecord) {
    if (dayRecord.dataset.dayReadonly === "true") toast("订阅日程只读", "请在原日历服务中修改，这里只显示缓存内容。", true);
    else {
      closeDayDetails();
      openEditor(dayRecord.dataset.dayDetailKind, dayRecord.dataset.dayDetailId);
    }
  }
  const todo = event.target.closest("[data-complete-todo]");
  if (todo) completeTodo(todo.dataset.completeTodo);
  const restore = event.target.closest("[data-restore-todo]");
  if (restore) restoreTodo(restore.dataset.restoreTodo);
  const reschedule = event.target.closest("[data-reschedule-todo]");
  if (reschedule) rescheduleTodo(reschedule.dataset.rescheduleTodo, reschedule.dataset.dueDate);
  const chooseTodoDate = event.target.closest("[data-choose-todo-date]");
  if (chooseTodoDate) openEditor("todo", chooseTodoDate.dataset.chooseTodoDate);
  const categoryView = event.target.closest("[data-category-view]");
  if (categoryView) { state.categoryView = categoryView.dataset.categoryView; render(); }
  const categoryDeletion = event.target.closest("[data-delete-category]");
  if (categoryDeletion) deleteCategory(categoryDeletion.dataset.deleteCategory);
  const exportButton = event.target.closest("[data-export-data]");
  if (exportButton) exportData(exportButton);
  const subscriptionButton = event.target.closest("[data-refresh-subscription]");
  if (subscriptionButton) refreshSubscription(subscriptionButton, subscriptionButton.dataset.refreshSubscription);
  if (event.target.closest("[data-add-subscription]")) { state.subscriptionEditorOpen = true; render(); }
  if (event.target.closest("[data-cancel-subscription]")) { state.subscriptionEditorOpen = false; render(); }
  if (event.target.closest("[data-confirm-subscription]")) addSubscription();
  const subscriptionDeletion = event.target.closest("[data-delete-subscription]");
  if (subscriptionDeletion) deleteSubscription(subscriptionDeletion.dataset.deleteSubscription);
  const restoreButton = event.target.closest("[data-restore-backup]");
  if (restoreButton) restoreLatestBackup(restoreButton);
  const aiTestButton = event.target.closest("[data-test-ai]");
  if (aiTestButton) testAi(aiTestButton);
  const backupTestButton = event.target.closest("[data-test-backup]");
  if (backupTestButton) testBackup(backupTestButton);
  const categoryToggle = event.target.closest("[data-category-toggle]");
  if (categoryToggle && !categoryToggle.disabled) {
    const categoryName = categoryToggle.dataset.categoryToggle;
    state.categoryExpanded[categoryName] = state.categoryExpanded[categoryName] === false;
    render();
  }
  if (event.target.closest("[data-onboarding-skip]")) completeOnboarding();
  if (event.target.closest("[data-onboarding-complete]")) completeOnboarding();
  const editor = event.target.closest("[data-edit-item]");
  if (editor) openEditor(editor.dataset.editItem, editor.dataset.itemId);
  const deletion = event.target.closest("[data-delete-item]");
  if (deletion) deleteItem(deletion.dataset.deleteItem, deletion.dataset.itemId);
  const relocateButton = event.target.closest("[data-relocate-data]");
  if (relocateButton) relocateData(relocateButton);
  const organizerSuggest = event.target.closest("[data-organizer-suggest]");
  if (organizerSuggest) requestOrganizerSuggestions(organizerSuggest);
  if (event.target.closest("[data-organizer-retry]")) retryOrganizerReview();
  if (event.target.closest("[data-organizer-apply]")) applyOrganizer();
  if (event.target.closest("[data-organizer-refresh]")) loadOrganizer();
  if (event.target.closest("[data-organizer-clear]")) clearOrganizerSuggestions();
  const bulkPreview = event.target.closest("[data-bulk-preview]");
  if (bulkPreview) openBulkPreview(bulkPreview.dataset.bulkPreview);
  if (event.target.closest("[data-bulk-confirm]")) applyBulkEdit();
  if (event.target.closest("[data-bulk-cancel]")) { state.bulkPreview = null; render(); }
  const bulkClear = event.target.closest("[data-bulk-clear]");
  if (bulkClear) clearBulkSelection(bulkClear.dataset.bulkClear);
  const organizerScope = event.target.closest("[data-organizer-scope]");
  if (organizerScope && organizerScope.dataset.organizerScope !== state.organizerScope) {
    state.organizerScope = organizerScope.dataset.organizerScope;
    if (state.organizerScope === "month") state.organizerMonth = state.month;
    loadOrganizer();
  }
});

document.querySelector("#close-composer").addEventListener("click", closeComposer);
document.querySelector("#close-day-detail").addEventListener("click", closeDayDetails);
backdrop.addEventListener("click", () => {
  if (composer.classList.contains("open")) closeComposer();
  else if (state.dayDetailsDate) closeDayDetails();
});
document.querySelector("#backup-button").addEventListener("click", backupNow);
document.querySelector("#menu-button").addEventListener("click", () => sidebar.classList.toggle("open"));
document.querySelectorAll(".entry-form").forEach((form) => {
  const button = form.querySelector("button[type=submit]");
  if (button) button.dataset.createText = button.textContent;
  form.addEventListener("submit", (event) => { event.preventDefault(); submitEntry(form); });
});
document.addEventListener("submit", (event) => {
  if (event.target.id === "settings-form") { event.preventDefault(); saveSettings(event.target); }
  if (event.target.id === "category-add-form" || event.target.matches(".category-child-form")) { event.preventDefault(); addCategory(event.target); }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "backup-backend") toggleBackupFields(event.target.value);
  if (event.target.id === "backup-proxy-mode") toggleProxyFields(event.target.value);
  if (event.target.matches("[data-toggle-subscription]")) toggleSubscription(event.target);
  if (event.target.matches('#form-transaction [name="categoryParent"]')) handleCategoryParentChange(event.target);
  if (event.target.matches('#form-transaction [name="categoryChild"]')) handleCategoryChildChange(event.target);
  if (event.target.matches("[data-organizer-check]")) {
    state.organizerSelections[event.target.dataset.organizerCheck][event.target.dataset.organizerId] = event.target.checked;
    render();
  }
  if (event.target.matches("[data-organizer-select]")) {
    state.organizerDrafts.transactions[event.target.dataset.organizerId] = event.target.value;
    render();
  }
  if (event.target.matches("[data-bulk-check]")) {
    const kind = event.target.dataset.bulkKind;
    state.bulkSelections[kind][event.target.dataset.bulkId] = event.target.checked;
    state.bulkPreview = null;
    render();
  }
  if (event.target.matches('[data-list-filter="expenseCategory"]')) {
    state.listFilters.expenseCategory = event.target.value;
    render();
  }
  if (event.target.matches('[data-list-filter="todoStatus"]')) {
    state.listFilters.todoStatus = event.target.value;
    render();
  }
  if (event.target.matches('[data-list-filter="diaryTag"]')) {
    state.listFilters.diaryTag = event.target.value;
    render();
  }
  if (event.target.matches("[data-bulk-draft]")) {
    state.bulkDrafts[event.target.dataset.bulkDraft] = event.target.value;
    render();
  }
});
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-category-quick-cancel]")) closeQuickCategory();
  if (event.target.closest("[data-category-quick-save]")) quickAddCategory();
});
document.addEventListener("input", (event) => {
  if (event.target.id === "global-search") {
    const value = event.target.value;
    if (value && state.route !== "search") state.searchReturnRoute = state.route;
    state.searchQuery = value;
    state.route = value ? "search" : state.searchReturnRoute;
    render();
  }
  if (event.target.matches('[data-list-filter="diaryTag"]')) state.listFilters.diaryTag = event.target.value;
  if (event.target.matches("[data-bulk-draft]")) state.bulkDrafts[event.target.dataset.bulkDraft] = event.target.value;
  if (event.target.matches("[data-organizer-tags]")) {
    const kind = event.target.dataset.organizerKind || "diary";
    state.organizerDrafts[kind][event.target.dataset.organizerId] = event.target.value;
    syncOrganizerApplyButton();
  }
});
document.querySelector('#form-event [name="allDay"]').addEventListener("change", (event) => {
  document.querySelector(".time-fields").hidden = event.target.checked;
});
document.addEventListener("keydown", (event) => {
  const customTrigger = event.target.closest("[data-custom-select-trigger]");
  const customOption = event.target.closest("[data-custom-select-option]");
  const customWrapper = (customTrigger || customOption)?.closest(".custom-select");
  if (customWrapper && customTrigger) {
    if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      if (customWrapper.classList.contains("open")) closeCustomSelect(customWrapper);
      else openCustomSelect(customWrapper);
      return;
    }
    if (event.key === "Escape" && customWrapper.classList.contains("open")) {
      event.preventDefault();
      closeCustomSelect(customWrapper, true);
      return;
    }
  }
  if (customWrapper && customOption) {
    if (event.key === "Tab") {
      closeCustomSelect(customWrapper);
      return;
    }
    const options = [...customWrapper.querySelectorAll("[data-custom-select-option]:not(:disabled)")];
    const currentIndex = options.indexOf(customOption);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const offset = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
      const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : Math.max(0, Math.min(options.length - 1, currentIndex + offset));
      options[nextIndex]?.focus();
      return;
    }
    if (["Enter", " "].includes(event.key)) {
      event.preventDefault();
      chooseCustomOption(customOption);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeCustomSelect(customWrapper, true);
      return;
    }
  }
  if (event.key === "Escape") {
    if (document.querySelector(".custom-select.open")) {
      closeCustomSelects();
      event.preventDefault();
      return;
    }
    if (composer.classList.contains("open")) closeComposer();
    else if (state.dayDetailsDate) closeDayDetails();
  }
  if ((event.key === "Enter" || event.key === " ") && event.target.closest("[data-calendar-date]")) {
    event.preventDefault();
    openDayDetails(event.target.closest("[data-calendar-date]").dataset.calendarDate);
  }
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) openComposer("transaction");
});

setDefaultDates();
enhanceCustomSelects();
loadSettings();
loadData();
setInterval(() => loadBackupStatus(false), 10000);

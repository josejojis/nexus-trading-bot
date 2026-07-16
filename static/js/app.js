class Bot {
    constructor() {
        this.apiBase = "/api";
        this.socket = null;
        this.visionChart = null;
        this.visionSeries = null;
        this.visionTrendSeries = null;
        this.visionPriceLines = [];
        this.lastVisualSymbol = null;
        this.logicTooltip = null;
        this.validationLogs = [];
        this.radarPage = 1;
        this.radarPageSize = 9;
        this.radarFilter = 'ALL';
        this.radarTrades = [];
        this.currentDecisionSignal = null;
        this.settingsNotificationTimer = null;
        this.init();
    }

    safeSetText(id, value) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = value;
        }
    }

    setAllInputs(id, value) {
        document.querySelectorAll(`#${id}`).forEach((el) => {
            if (el.type === 'checkbox') {
                el.checked = Boolean(value);
            } else {
                el.value = value;
            }
        });
    }

    findSettingInput(id) {
        const selector = `#${id}`;
        const visible = Array.from(document.querySelectorAll(selector)).find((el) => {
            return el.offsetParent !== null || el.closest('.modal')?.style.display === 'block';
        });
        return visible || document.querySelector(selector);
    }

    readFormValue(form, id, fallback = '') {
        const scoped = form?.querySelector(`#${id}`);
        const el = scoped || this.findSettingInput(id);
        if (!el) return fallback;
        return el.type === 'checkbox' ? el.checked : el.value;
    }

    readNumber(form, id, fallback = 0) {
        const raw = this.readFormValue(form, id, fallback);
        if (raw === '' || raw === null || raw === undefined) return fallback;
        const value = Number(raw);
        return Number.isFinite(value) ? value : fallback;
    }

    readPercentDecimal(form, id, fallback = 0) {
        const value = this.readNumber(form, id, fallback);
        return value > 1 ? value / 100 : value;
    }

    compactSettingsView() {
        const settings = document.getElementById('settings');
        if (!settings) return;

        const coreIds = new Set([
            'symbols',
            'volume',
            'smallAccountModeEnabled',
            'smallAccountThreshold',
            'smallAccountTradeVolume',
            'smallAccountMaxExposurePct',
            'smallAccountMaxActiveTrades',
            'smallAccountMaxAutoMinLot',
            'smallAccountAllowMetals',
            'smallAccountAllowCrypto',
            'smallAccountAllowStocks',
            'maxExposurePct',
            'dailyProfitCap',
            'maxTradesPerSymbol',
            'tradeCooldownMinutes',
            'professionalGateEnabled',
            'minExecutionGrade',
            'minProfessionalScore',
            'minProfessionalConviction',
            'earlyEntryEnabled',
            'earlyEntryMinScore',
            'featureIctMode',
            'trailingStopTriggerPct',
            'trailingStopLockPips',
            'trailingStopStepPct',
            'partialTpEnabled',
            'partialTpTriggerR',
            'partialTpClosePct',
            'partialTpLockPips',
            'opposingSignalProfitExitEnabled',
            'opposingSignalMinR',
            'warRoomEnabled',
        ]);

        settings.classList.add('settings-core-mode');
        settings.querySelectorAll('.form-group').forEach((group) => {
            const control = group.querySelector('input, select, textarea');
            if (!control || !coreIds.has(control.id)) {
                group.classList.add('settings-advanced-field');
            } else {
                group.classList.remove('settings-advanced-field');
            }
        });

        settings.querySelectorAll('.settings-section').forEach((section) => {
            const visibleCount = section.querySelectorAll('.form-group:not(.settings-advanced-field)').length;
            section.classList.toggle('settings-hidden-section', visibleCount === 0);
        });

        if (!document.getElementById('settingsModeToggle')) {
            const tabbar = settings.querySelector('.settings-tabbar');
            const button = document.createElement('button');
            button.id = 'settingsModeToggle';
            button.type = 'button';
            button.className = 'settings-tab settings-mode-toggle';
            button.textContent = 'Show Advanced';
            button.addEventListener('click', () => {
                const advanced = settings.classList.toggle('settings-show-advanced');
                button.textContent = advanced ? 'Hide Advanced' : 'Show Advanced';
            });
            tabbar?.appendChild(button);
        }

        if (!document.getElementById('settingsUiVersion')) {
            const header = settings.querySelector('.settings-header');
            const marker = document.createElement('span');
            marker.id = 'settingsUiVersion';
            marker.className = 'settings-ui-version';
            marker.textContent = 'Core settings UI v2';
            header?.appendChild(marker);
        }
    }

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    showNotification(message, type = 'success') {
        const ids = ['settingsNotification', 'settingsModalNotification'];
        let shown = false;
        ids.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = message;
            el.className = `settings-notification ${type}`;
            el.style.display = 'block';
            el.setAttribute('role', type === 'error' ? 'alert' : 'status');
            el.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');
            shown = true;
        });
        if (!shown) {
            alert(message);
            return;
        }
        if (this.settingsNotificationTimer) {
            window.clearTimeout(this.settingsNotificationTimer);
        }
        this.settingsNotificationTimer = window.setTimeout(() => {
            ids.forEach((id) => {
                const el = document.getElementById(id);
                if (el) {
                    el.style.display = 'none';
                }
            });
        }, 5000);
    }

    formatLogValue(value) {
        if (value === null || value === undefined) return '-';
        if (Array.isArray(value)) {
            if (!value.length) return '-';
            if (value.every((item) => typeof item !== 'object')) {
                return `<span>${value.map((item) => this.escapeHtml(item)).join(', ')}</span>`;
            }
            return `<div class="log-chip-list">${value.slice(0, 8).map((item) => this.formatObjectSummary(item)).join('')}</div>`;
        }
        if (typeof value === 'object') {
            return this.formatObjectSummary(value);
        }
        return this.escapeHtml(value);
    }

    humanEventName(event = '') {
        const names = {
            TRADE_EXECUTED: 'Trade Executed',
            TRADE_CLOSED: 'Trade Closed',
            SIGNAL_REJECTED: 'Signal Rejected',
            FVG_DETECTED: 'Signal Detected',
            PARTIAL_TP: 'Partial Take Profit',
            PARTIAL_TP_SL_LOCK: 'Partial TP Stop Locked',
            PARTIAL_TP_RUNNER_TP: 'Runner TP Assigned',
            REVERSE_PROFIT_EXIT: 'Reverse Profit Exit',
            MAX_ADVERSE_EXIT: 'Max Adverse Exit',
            NEWS_LADDER_ADDON: 'News Ladder Add-On',
            REVERSAL_SHOCK_GUARD: 'Reversal Shock Guard',
            OPPOSING_SIGNAL_PROFIT_EXIT: 'Opposing Signal Profit Exit',
        };
        return names[event] || this.labelize(event);
    }

    labelize(key = '') {
        return String(key)
            .replace(/_/g, ' ')
            .replace(/\b\w/g, (m) => m.toUpperCase());
    }

    formatNumber(value, digits = 2) {
        const num = Number(value);
        return Number.isFinite(num) ? num.toFixed(digits) : '-';
    }

    formatObjectSummary(obj = {}) {
        if (!obj || typeof obj !== 'object') return this.escapeHtml(obj);

        if (obj.label || obj.key || obj.passed !== undefined) {
            const label = obj.label || this.labelize(obj.key || 'Component');
            const tone = obj.passed === true ? 'pass' : obj.passed === false ? 'fail' : '';
            const detail = obj.detail || obj.description || '';
            const points = obj.points != null && obj.max_points != null ? ` ${obj.points}/${obj.max_points}` : '';
            return `<span class="setup-chip ${tone}" title="${this.escapeHtml(detail)}">${this.escapeHtml(label)}${points}</span>`;
        }

        if (obj.type || obj.hold_time || obj.r_ratio != null) {
            const parts = [
                obj.type,
                obj.hold_time,
                obj.r_ratio != null ? `${this.formatNumber(obj.r_ratio, 2)}R` : null,
                obj.risk_pips != null ? `risk ${this.formatNumber(obj.risk_pips, 1)}p` : null,
                obj.reward_pips != null ? `reward ${this.formatNumber(obj.reward_pips, 1)}p` : null,
            ].filter(Boolean);
            const reason = obj.reason || obj.description || '';
            return `<div class="human-log-summary"><strong>${this.escapeHtml(parts.join(' | ') || 'Profile')}</strong>${reason ? `<small>${this.escapeHtml(reason)}</small>` : ''}</div>`;
        }

        if (obj.snapshots || obj.aligned || obj.conflicting || obj.reason) {
            const score = obj.score != null ? `MTF ${this.formatNumber(obj.score, 2)}` : 'MTF';
            const aligned = Array.isArray(obj.aligned) && obj.aligned.length ? `Aligned: ${obj.aligned.join(', ')}` : '';
            const conflicting = Array.isArray(obj.conflicting) && obj.conflicting.length ? `Conflict: ${obj.conflicting.join(', ')}` : '';
            const reason = obj.reason || [aligned, conflicting].filter(Boolean).join(' | ');
            return `<div class="human-log-summary"><strong>${this.escapeHtml(score)}</strong>${reason ? `<small>${this.escapeHtml(reason)}</small>` : ''}</div>`;
        }

        if (obj.score != null || obj.grade || obj.archetype || obj.summary) {
            const header = [
                obj.grade ? `Grade ${obj.grade}` : null,
                obj.archetype,
                obj.score != null ? `score ${this.formatNumber(obj.score, 2)}` : null,
            ].filter(Boolean).join(' | ');
            const components = Array.isArray(obj.components)
                ? `<div class="log-chip-list">${obj.components.slice(0, 10).map((item) => this.formatObjectSummary(item)).join('')}</div>`
                : '';
            return `<div class="human-log-summary"><strong>${this.escapeHtml(header || 'Setup')}</strong>${obj.summary ? `<small>${this.escapeHtml(obj.summary)}</small>` : ''}${components}</div>`;
        }

        if (obj.safe !== undefined || obj.spread_pips != null || obj.max_spread_pips != null) {
            const tone = obj.safe === true ? 'pass' : obj.safe === false ? 'fail' : '';
            const spread = obj.spread_pips != null ? `${this.formatNumber(obj.spread_pips, 2)}p` : 'n/a';
            const max = obj.max_spread_pips != null ? ` / max ${this.formatNumber(obj.max_spread_pips, 2)}p` : '';
            return `<span class="setup-chip ${tone}">${obj.safe === false ? 'Unsafe' : 'Safe'} spread ${spread}${max}</span>`;
        }

        if (obj.mode || obj.plan || obj.direction || obj.zone) {
            const parts = [obj.mode, obj.plan, obj.direction, obj.zone].filter(Boolean).join(' | ');
            return `<div class="human-log-summary"><strong>${this.escapeHtml(parts || 'Context')}</strong>${obj.description ? `<small>${this.escapeHtml(obj.description)}</small>` : ''}</div>`;
        }

        const simpleRows = Object.entries(obj)
            .filter(([, v]) => v === null || ['string', 'number', 'boolean'].includes(typeof v))
            .slice(0, 8);
        if (simpleRows.length) {
            return `<div class="human-log-summary">${simpleRows.map(([k, v]) => `<span><strong>${this.escapeHtml(this.labelize(k))}:</strong> ${this.escapeHtml(v)}</span>`).join('')}</div>`;
        }

        return `<pre class="log-json">${this.escapeHtml(JSON.stringify(obj, null, 2))}</pre>`;
    }

    getMtfDetails(signal = {}) {
        return signal?.multi_timeframe
            || signal?.setup_score?.multi_timeframe
            || signal?.analytic?.multi_timeframe
            || null;
    }

    getMtfSummary(signal = {}) {
        const mtf = this.getMtfDetails(signal);
        if (!mtf || typeof mtf !== 'object') {
            return {score: null, label: 'MTF pending', detail: 'No multi-timeframe analytic result yet', tone: ''};
        }
        const score = Number(mtf.score);
        const tone = Number.isFinite(score) ? (score >= 0.62 ? 'pass' : score >= 0.45 ? 'warn' : 'fail') : '';
        const aligned = Array.isArray(mtf.aligned) ? mtf.aligned : [];
        const conflicting = Array.isArray(mtf.conflicting) ? mtf.conflicting : [];
        const detailParts = [
            mtf.reason,
            aligned.length ? `Aligned ${aligned.join(', ')}` : null,
            conflicting.length ? `Conflict ${conflicting.join(', ')}` : null,
        ].filter(Boolean);
        return {
            score: Number.isFinite(score) ? score : null,
            label: Number.isFinite(score) ? `MTF ${(score * 100).toFixed(0)}%` : 'MTF active',
            detail: detailParts.join(' | ') || 'Multi-timeframe scan active',
            tone,
        };
    }

    getExecutionMethod(signal = {}) {
        const horizon = String(signal?.trade_horizon?.type || signal?.horizon_profile || 'INTRADAY').toUpperCase();
        const symbol = String(signal?.symbol || '').toUpperCase();
        const asset = signal?.instrument_class
            || signal?.instrument_profile?.asset_class
            || (symbol.includes('XAU') || symbol.includes('XAG') ? 'METAL' : /^[A-Z]{6}$/.test(symbol) ? 'FOREX' : 'OTHER');
        const methodByHorizon = {
            SCALP: 'Scalp: fast structure confirmation',
            INTRADAY: 'Intraday: balanced structure and session',
            SWING: 'Swing: HTF alignment required',
        };
        return {
            horizon,
            asset,
            method: methodByHorizon[horizon] || 'Adaptive execution',
        };
    }

    renderExecutionMethodChips(signal = {}) {
        const method = this.getExecutionMethod(signal);
        const mtf = this.getMtfSummary(signal);
        return `
            <span class="setup-chip method-chip">${this.escapeHtml(method.asset)}</span>
            <span class="setup-chip method-chip">${this.escapeHtml(method.horizon)}</span>
            <span class="setup-chip method-chip" title="${this.escapeHtml(method.method)}">${this.escapeHtml(method.method)}</span>
            <span class="setup-chip mtf-chip ${mtf.tone}" title="${this.escapeHtml(mtf.detail)}">${this.escapeHtml(mtf.label)}</span>
        `;
    }

    getExecutionMethodText(signal = {}) {
        const method = this.getExecutionMethod(signal);
        const mtf = this.getMtfSummary(signal);
        return `${method.asset} | ${method.horizon} | ${method.method} | ${mtf.label}`;
    }

    summarizeLogDetails(log = {}) {
        const preferred = [
            'event', 'symbol', 'action', 'type', 'nature', 'trade_style', 'execution_type',
            'setup_score', 'trade_horizon', 'multi_timeframe', 'spread_safety', 'false_move', 'news_move',
            'instrument_class', 'instrument_profile', 'symbol_profile', 'horizon_profile', 'management_profile',
            'stage', 'qualification', 'trade_readiness',
            'reason', 'r', 'profit', 'close_pct', 'runner_tp', 'partial_runner_tp',
            'closed_action', 'opposing_action', 'shock_direction', 'shock_until',
        ];
        const seen = new Set();
        const ordered = [];
        preferred.forEach((key) => {
            if (Object.prototype.hasOwnProperty.call(log, key)) {
                ordered.push([key, log[key]]);
                seen.add(key);
            }
        });
        Object.entries(log).forEach(([key, value]) => {
            if (!seen.has(key) && key !== 'timestamp') ordered.push([key, value]);
        });
        return ordered;
    }

    getSignalQualityScore(signal = {}) {
        const setup = signal?.setup_score || {};
        const raw = setup.score
            ?? signal.confluence_score
            ?? signal.trade_readiness?.score
            ?? signal.readiness_score
            ?? signal.conviction_score
            ?? signal.conviction
            ?? signal.score
            ?? 0;
        const value = Number(raw) || 0;
        return value > 1 ? value / 100 : value;
    }

    getBestSignalBadge(signal = {}) {
        const grade = String(signal?.setup_score?.grade || signal.grade || '').toUpperCase();
        const score = this.getSignalQualityScore(signal);
        if (grade === 'A' || score >= 0.78 || signal.best_signal === true || signal._bestSignal === true) {
            return '<span class="best-signal-badge"><i class="fas fa-crown"></i> Best Setup</span>';
        }
        return '';
    }

    isBestSignal(signal = {}, topScore = null) {
        const grade = String(signal?.setup_score?.grade || signal.grade || '').toUpperCase();
        const score = this.getSignalQualityScore(signal);
        const isTop = topScore != null && score > 0 && Math.abs(score - topScore) < 0.0001;
        return signal.best_signal === true || signal._bestSignal === true || grade === 'A' || score >= 0.78 || isTop;
    }

    createLogAccordionItem(log, title, meta = '', options = {}) {
        const item = document.createElement('div');
        const best = this.isBestSignal(log, options.topScore);
        item.className = `accordion-item log-accordion-item${best ? ' best-signal' : ''}`;
        const executable = this.isExecutableSignal(log);
        const badge = best ? this.getBestSignalBadge({...log, best_signal: true}) : '';

        const details = this.summarizeLogDetails(log || {})
            .map(([key, value]) => `
                <div class="log-detail-row">
                    <strong>${this.escapeHtml(this.labelize(key))}</strong>
                    <span>${this.formatLogValue(value)}</span>
                </div>
            `)
            .join('');

        item.innerHTML = `
            <button class="accordion-header" type="button">
                <span class="accordion-title">${this.escapeHtml(title)} ${badge}</span>
                <span class="accordion-meta">${this.escapeHtml(meta)} <i class="fas fa-chevron-down"></i></span>
            </button>
            <div class="accordion-details">
                <div class="log-detail-actions">
                    ${executable ? '<button class="btn-small btn-place signal-log-execute-btn" type="button"><i class="fas fa-play"></i> Execute</button>' : ''}
                    <button class="btn-small btn-reset log-popup-btn" type="button">Open Popup</button>
                </div>
                ${details}
            </div>
        `;

        const header = item.querySelector('.accordion-header');
        const detailPanel = item.querySelector('.accordion-details');
        header.addEventListener('click', () => {
            detailPanel.classList.toggle('expanded');
            item.classList.toggle('open');
        });
        item.querySelector('.log-popup-btn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showLogDetails(log);
        });
        item.querySelector('.signal-log-execute-btn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.executeSignal(log, null, 'payload');
        });
        return item;
    }

    formatMoney(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '--';
        }
        const amount = Number(value);
        const sign = amount > 0 ? '+' : '';
        return `${sign}$${amount.toFixed(2)}`;
    }

    setMoney(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        const amount = Number(value);
        el.textContent = this.formatMoney(value);
        el.classList.toggle('metric-profit', Number.isFinite(amount) && amount > 0);
        el.classList.toggle('metric-loss', Number.isFinite(amount) && amount < 0);
    }

    formatTime(value) {
        if (!value) return '--';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '--';
        return parsed.toLocaleTimeString();
    }

    renderScanStatus(scan) {
        const data = scan || {};
        const mode = data.on_new_candle
            ? `M${data.timeframe_minutes || 5} candle`
            : `${data.interval_seconds || 5}s loop`;
        this.safeSetText('scanMode', mode);
        this.safeSetText('lastScan', this.formatTime(data.last_scan_at));
        const seconds = Number(data.seconds_until_next_scan);
        this.safeSetText('nextScan', Number.isFinite(seconds) ? `${Math.max(0, Math.round(seconds))}s` : '--');
        const cooldown = data.trade_cooldown_minutes != null ? `${data.trade_cooldown_minutes}m` : '--';
        const maxTrades = data.max_trades_per_symbol != null ? ` | ${data.max_trades_per_symbol}/symbol` : '';
        const maxTotal = data.max_active_trades_total != null ? ` | ${data.max_active_trades_total} total` : '';
        this.safeSetText('tradeCooldown', `${cooldown}${maxTrades}${maxTotal}`);
    }

    formatProfitProtectionState(tm = {}) {
        const parts = [];
        if (tm.trailing_tp) parts.push(`Trail ${Math.round((tm.trailing_tp_trigger_pct || 0) * 100)}%`);
        if (tm.partial_tp) parts.push(`Part ${Number(tm.partial_tp_trigger_r || 0).toFixed(2)}R`);
        if (tm.partial_tp_extend) parts.push(`Runner +${Math.round((tm.partial_tp_extend_pct || 0) * 100)}%`);
        if (tm.reverse_profit_exit) parts.push('Reverse');
        if (tm.opposing_signal_profit_exit) parts.push(`Opp ${Number(tm.opposing_signal_min_r || 0).toFixed(2)}R`);
        return parts.length ? parts.join(' | ') : 'Off';
    }

    renderExecutionFeatureState(tm = {}, status = {}) {
        const ictOn = Boolean(tm.ict_mode || status.ict_mode);
        this.safeSetText('ictMode', ictOn ? 'Enabled' : 'Disabled');

        const profileParts = [];
        if (tm.symbol_profiles_enabled) profileParts.push('Symbol');
        if (tm.instrument_profiles_enabled) profileParts.push('Instrument');
        if (tm.trade_horizon_profiles) profileParts.push('Horizon');
        if (tm.horizon_profile_mode) profileParts.push(String(tm.horizon_profile_mode).replace('_', ' '));
        this.safeSetText('profileState', profileParts.length ? profileParts.join(' + ') : 'Off');

        const eventGuard = [
            tm.false_move_detection ? 'Trap' : null,
            tm.news_mode ? `News ${Math.round((tm.news_risk_multiplier || 1) * 100)}%` : null,
            tm.news_ladder ? `Ladder ${tm.news_ladder_max_addons || 0}x` : null
        ].filter(Boolean).join(' + ');
        this.safeSetText('eventGuardState', eventGuard || 'Off');

        this.safeSetText(
            'opposingExitState',
            tm.opposing_signal_profit_exit
                ? `On ${Number(tm.opposing_signal_min_r || 0).toFixed(2)}R / ${Number(tm.opposing_signal_min_score || 0).toFixed(2)}`
                : 'Off'
        );
        const scan = status.scan || {};
        this.safeSetText(
            'shockGuardState',
            scan.reversal_shock_guard
                ? `On ${scan.reversal_shock_cooldown_minutes || 0}m`
                : 'Off'
        );
    }

    init() {
        this.setupListeners();
        this.setupSocket();
        this.setupVisionChart();
        this.updateDashboard();
        this.loadSignals();
        this.loadSessions();
        this.loadKillStatus();
        this.loadFutureTrades();
        this.loadApiEndpoints();
        this.loadSettings();

        this.setupVisionCardClicks();

        setInterval(() => this.updateDashboard(), 6000);
        setInterval(() => this.loadSignals(), 8000);
        setInterval(() => this.loadKillStatus(), 10000);
        setInterval(() => {
            if (document.getElementById("future-trades")?.classList.contains("active")) {
                this.loadFutureTrades();
            }
        }, 15000);
    }

    setupListeners() {
        document.getElementById("startBtn").addEventListener("click", () =>
            this.start()
        );
        document.getElementById("stopBtn").addEventListener("click", () =>
            this.stop()
        );

        document.querySelectorAll(".nav-link").forEach((link) => {
            link.addEventListener("click", (e) => this.navigate(e));
        });

        // kill switch controls
        document.getElementById('killToggle')?.addEventListener('change', () => this.toggleKill());
        document.getElementById('killSymbol')?.addEventListener('change', () => this.loadKillStatus());
        document.getElementById('decisionExecuteBtn')?.addEventListener('click', (event) => {
            event.stopPropagation();
            if (this.currentDecisionSignal) {
                this.executeSignal(this.currentDecisionSignal, null, 'payload');
            }
        });

        // quick action buttons
        document.getElementById('approveSignalBtn')?.addEventListener('click', () => this.approveSignal());
        document.getElementById('rejectSignalBtn')?.addEventListener('click', () => this.rejectSignal());
        document.getElementById('pauseBtn')?.addEventListener('click', () => this.togglePause());
        document.getElementById('panicCloseBtn')?.addEventListener('click', () => this.panicClose());
        document.getElementById('panicCloseBtnTop')?.addEventListener('click', () => this.panicClose());
        document.getElementById('symbolSearch')?.addEventListener('input', (e) => this.filterSymbolLists(e.target.value));
        document.getElementById('radarPrevBtn')?.addEventListener('click', () => this.changeRadarPage(-1));
        document.getElementById('radarNextBtn')?.addEventListener('click', () => this.changeRadarPage(1));
        document.querySelectorAll('.radar-filter').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.radar-filter').forEach((x) => x.classList.remove('active'));
                btn.classList.add('active');
                this.radarFilter = btn.dataset.filter || 'ALL';
                this.radarPage = 1;
                this.renderGlobalRadar();
            });
        });

        // settings form
        document.getElementById('settingsForm')?.addEventListener('submit', (e) => this.saveSettings(e));
        document.getElementById('settingsModalForm')?.addEventListener('submit', (e) => this.saveSettings(e));
        document.querySelectorAll('.settings-save-group').forEach((btn) => {
            btn.addEventListener('click', (e) => this.saveSettingsGroup(e));
        });
        document.getElementById('resetBtn')?.addEventListener('click', () => this.loadSettings());
        document.getElementById('exportBtn')?.addEventListener('click', () => this.exportSettings());
        document.querySelectorAll('.settings-tab').forEach((btn) => {
            btn.addEventListener('click', () => this.setSettingsTab(btn.dataset.settingsTab || 'basic'));
        });
        this.compactSettingsView();

        // modal controls
        document.querySelectorAll('.close').forEach((btn) => {
            btn.addEventListener('click', () => {
                const modal = btn.closest('.modal');
                if (modal) {
                    modal.style.display = 'none';
                }
            });
        });
        window.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                e.target.style.display = 'none';
            }
        });

        // bento card click handlers (legacy) 
        document.querySelectorAll('.bento-card').forEach((card) => {
            card.addEventListener('click', (e) => {
                const popupId = card.dataset.popup;
                if (popupId) {
                    this.openPopup(popupId);
                }
            });
        });

        // full-card detail modal behavior
        document.querySelectorAll('.detail-card').forEach((card) => {
            card.addEventListener('click', () => {
                const title = card.dataset.cardTitle || 'Card Details';
                const details = card.dataset.cardDetails || '';
                const renderType = card.dataset.cardRender || 'default';
                const bodyElement = document.getElementById('cardDetailsBody');
                const titleElement = document.getElementById('cardDetailsTitle');
                if (titleElement) titleElement.textContent = title;
                if (bodyElement) {
                    bodyElement.innerHTML = '';
                    if (renderType === 'table') {
                        const rows = [];
                        card.querySelectorAll('[data-detail]').forEach((field) => {
                            const key = field.dataset.detail || field.textContent.trim();
                            const value = field.textContent.trim();
                            rows.push(`<tr><td><strong>${key}:</strong></td><td>${value}</td></tr>`);
                        });
                        bodyElement.innerHTML = `<table class="card-details-table">${rows.join('')}</table>`;
                    } else if (renderType === 'list') {
                        const items = [];
                        card.querySelectorAll('[data-detail]').forEach((field) => {
                            const key = field.dataset.detail || field.textContent.trim();
                            const value = field.textContent.trim();
                            items.push(`<li><strong>${key}:</strong> ${value}</li>`);
                        });
                        bodyElement.innerHTML = `<ul class="card-details-list">${items.join('')}</ul>`;
                    } else {
                        bodyElement.innerHTML = `<div class="card-details-item"><strong>${title}</strong><p>${details}</p></div>`;
                    }
                }
                const modal = document.getElementById('cardDetailsModal');
                if (modal) modal.style.display = 'block';
            });
        });

        // Strategy bento actions
        document.getElementById('seeAllSignalsBtn')?.addEventListener('click', () => {
            const modal = document.getElementById('cardDetailsModal');
            const titleElement = document.getElementById('cardDetailsTitle');
            const bodyElement = document.getElementById('cardDetailsBody');
            if (titleElement) titleElement.textContent = 'All Signals';
            if (bodyElement) {
                // Populate with signals list (mock or from data)
                const signals = [
                    { symbol: 'EURUSD', type: 'BUY', summary: 'Strong uptrend' },
                    { symbol: 'GBPUSD', type: 'SELL', summary: 'Overbought' }
                ];
                const items = signals.map(s => `<li><strong>${s.symbol} ${s.type}:</strong> ${s.summary}</li>`).join('');
                bodyElement.innerHTML = `<ul class="card-details-list">${items}</ul>`;
            }
            if (modal) modal.style.display = 'block';
        });

        document.getElementById('openSettingsModal')?.addEventListener('click', () => {
            document.querySelectorAll('.modal').forEach((modal) => {
                modal.style.display = 'none';
                modal.classList.remove('open');
            });
            const settingsLink = document.querySelector('.nav-link[data-page="settings"]');
            if (settingsLink) {
                settingsLink.click();
            } else {
                document.querySelectorAll('.page').forEach((page) => page.classList.remove('active'));
                document.getElementById('settings')?.classList.add('active');
            }
            this.loadSettings();
        });

        document.getElementById('cancelSettingsBtn')?.addEventListener('click', () => {
            const modal = document.getElementById('settingsModal');
            if (modal) modal.style.display = 'none';
        });

        document.querySelectorAll('.tab-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach((x) => x.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
            });
        });

        // modal actions
        document.getElementById('approveCardBtn')?.addEventListener('click', () => this.approveCard());
        document.getElementById('rejectCardBtn')?.addEventListener('click', () => this.rejectCard());

        const tooltip = document.getElementById('logicTagTooltip');
        this.logicTooltip = tooltip;
    }

    openPopup(popupId) {
        const modal = document.getElementById(popupId);
        if (modal) {
            modal.style.display = 'block';
            // Populate popup data based on popupId
            this.populatePopupData(popupId);
        }
    }

    populatePopupData(popupId) {
        switch(popupId) {
            case 'equity-popup':
                this.populateEquityPopup();
                break;
            case 'drawdown-popup':
                this.populateDrawdownPopup();
                break;
            case 'status-popup':
                this.populateStatusPopup();
                break;
            case 'performance-popup':
                this.populatePerformancePopup();
                break;
            case 'top-pair-popup':
                this.populateTopPairPopup();
                break;
            case 'session-popup':
                this.populateSessionPopup();
                break;
            case 'risk-popup':
                this.populateRiskPopup();
                break;
            case 'news-popup':
                this.populateNewsPopup();
                break;
        }
    }

    populateEquityPopup() {
        // Get current equity data from dashboard elements
        const equity = document.getElementById('equity')?.textContent || '$0.00';
        const dailyChange = document.getElementById('dailyChange')?.textContent || '+0.00%';

        this.safeSetText('popupBalance', equity);
        this.safeSetText('popupEquity', equity);
        this.safeSetText('popupDailyChange', dailyChange);
        // Note: Floating P&L would need to be calculated from positions data
        this.safeSetText('popupFloating', '$0.00');
    }

    populateDrawdownPopup() {
        // This would populate with current open positions and their P&L
        const drawdownTrades = document.getElementById('drawdownTrades');
        if (drawdownTrades) {
            drawdownTrades.innerHTML = '<p>No active positions to display</p>';
        }
    }

    populateStatusPopup() {
        // Get status data from dashboard
        const status = document.getElementById('status')?.textContent || 'Unknown';
        this.safeSetText('mt5Connection', status === 'Running' ? 'Connected' : 'Disconnected');
        this.safeSetText('apiHeartbeat', '-- ms');
        this.safeSetText('botVersion', '1.0.0');
        this.safeSetText('uptime', '00:00:00');

        const statusLogs = document.getElementById('statusLogs');
        if (statusLogs) {
            statusLogs.innerHTML = '<div>Bot initialized successfully</div><div>MT5 connection established</div>';
        }
    }

    populatePerformancePopup() {
        // Get performance data from dashboard
        const winRate = document.getElementById('winRate')?.textContent || '0%';
        const profitFactor = document.getElementById('profitFactor')?.textContent || '0.00';

        this.safeSetText('popupWinRate', winRate);
        this.safeSetText('popupProfitFactor', profitFactor);
        this.safeSetText('popupAvgWin', '$0.00');
        this.safeSetText('popupAvgLoss', '$0.00');
        this.safeSetText('popupTotalTrades', '0');
        this.safeSetText('popupExpectancy', '$0.00');
    }

    populateTopPairPopup() {
        // This would show the best performing pair from recent trades
        this.safeSetText('popupTopSymbol', 'EURUSD');
        this.safeSetText('popupTopChange', '+2.45%');
        this.safeSetText('topPairTrades', '12');
        this.safeSetText('topPairWinRate', '75%');
        this.safeSetText('topPairPnL', '$245.67');
    }

    populateSessionPopup() {
        // Update session volatilities based on current market conditions
        const sessions = ['tokyo', 'london', 'ny'];
        sessions.forEach(session => {
            const element = document.getElementById(`${session}Volatility`);
            if (element) {
                element.textContent = session === 'london' ? 'High' : 'Low';
                element.setAttribute('data-volatility', session === 'london' ? 'high' : 'low');
            }
        });
    }

    populateRiskPopup() {
        // Get risk data from dashboard
        this.safeSetText('popupMarginUsed', '$0.00');
        this.safeSetText('popupMarginFree', '$10,000.00');
        this.safeSetText('popupMarginLevel', '1000%');
        this.safeSetText('popupMaxExposure', '5%');
    }

    populateNewsPopup() {
        // This would load news events from an API
        const newsEvents = document.getElementById('newsEvents');
        if (newsEvents) {
            newsEvents.innerHTML = '<div>No recent news events</div>';
        }
    }

    panicClose() {
        if (confirm('Are you sure you want to close all positions? This action cannot be undone.')) {
            fetch(`${this.apiBase}/panic-close`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
            })
            .then(response => response.json())
            .then(data => {
                alert('Panic close initiated. All positions will be closed.');
                // Close the modal
                document.getElementById('drawdown-popup').style.display = 'none';
            })
            .catch(error => {
                console.error('Error initiating panic close:', error);
                alert('Error initiating panic close. Please try again.');
            });
        }
    }

    filterSymbolLists(query) {
        const needle = String(query || '').trim().toUpperCase();
        ['pendingOrdersTable', 'positionsTable'].forEach((id) => {
            const root = document.getElementById(id);
            if (!root) return;
            root.querySelectorAll('li, tr').forEach((row) => {
                const text = row.textContent.toUpperCase();
                row.style.display = !needle || text.includes(needle) ? '' : 'none';
            });
        });
    }

    approveCard() {
        alert('Card approved!');
        // Add logic to approve the card (e.g., send to backend)
        document.getElementById('cardDetailsModal').style.display = 'none';
    }

    rejectCard() {
        alert('Card rejected!');
        // Add logic to reject the card (e.g., send to backend)
        document.getElementById('cardDetailsModal').style.display = 'none';
    }

    setupSocket() {
        if (!window.io) return;

        this.socket = io();

        this.socket.on('connect', () => {
            console.info('Socket.IO connected');
        });

        this.socket.on('disconnect', () => {
            console.info('Socket.IO disconnected');
        });

        this.socket.on('dashboard_update', (payload) => {
            this.applyRealtimePayload(payload);
        });

        this.socket.on('validation_logs', (payload) => {
            this.validationLogs = payload || [];
            this.renderValidationLogs();
        });
    }

    setupVisionChart() {
        const container = document.getElementById('visionChart');
        if (!container || !window.LightweightCharts) return;

        container.innerHTML = '';
        this.visionChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 220,
            layout: {
                background: { color: '#0f172a' },
                textColor: '#e2e8f0',
            },
            grid: {
                vertLines: { color: 'rgba(148, 163, 184, 0.1)' },
                horzLines: { color: 'rgba(148, 163, 184, 0.1)' },
            },
            crosshair: {
                mode: 0,
            },
            timeScale: {
                visible: false,
                borderColor: 'rgba(148, 163, 184, 0.3)',
            },
        });

        if (this.visionChart && typeof this.visionChart.addLineSeries === 'function') {
            this.visionSeries = this.visionChart.addLineSeries({
                color: '#60a5fa',
                lineWidth: 2,
            });
        } else if (this.visionChart && typeof this.visionChart.addAreaSeries === 'function') {
            console.warn('addLineSeries not available; using addAreaSeries fallback');
            this.visionSeries = this.visionChart.addAreaSeries({
                topColor: 'rgba(96, 165, 250, 0.5)',
                bottomColor: 'rgba(96, 165, 250, 0.01)',
                lineColor: '#60a5fa',
                lineWidth: 2,
            });
        } else {
            console.error('Vision chart: no series method available');
            this.visionSeries = null;
        }

        if (this.visionChart && typeof this.visionChart.addLineSeries === 'function') {
            this.visionTrendSeries = this.visionChart.addLineSeries({
                color: '#f59e0b',
                lineWidth: 2,
                lineStyle: 2,
                priceLineVisible: false,
                lastValueVisible: false,
            });
        }
    }

    applyRealtimePayload(payload) {
        if (!payload) return;

        const status = payload.status || {};
        this.safeSetText('botStatus', status.running ? 'Online' : 'Offline');
        const statusIcon = document.getElementById('statusIcon');
        if (statusIcon) statusIcon.className = status.running ? 'fas fa-circle online' : 'fas fa-circle';
        this.safeSetText('connected', status.connected ? 'Connected' : 'Disconnected');
        this.safeSetText('balance', `$${(status.balance || 0).toFixed(2)}`);
        this.safeSetText('equity', `$${(status.equity || 0).toFixed(2)}`);
        this.safeSetText('margin', `$${(status.free_margin || 0).toFixed(2)}`);
        this.setMoney('dailyProfit', status.daily_profit);
        this.setMoney('floatingProfit', status.floating_profit);
        this.setMoney('realizedProfit', status.realized_profit);
        this.setMoney('netProfit', status.net_profit);
        this.setMoney('drawdown', status.floating_drawdown);
        const realtimeActiveTrades = Array.isArray(payload.positions) ? payload.positions.length : (status.active_trades || 0);
        this.safeSetText('activeTrades', realtimeActiveTrades);
        this.renderScanStatus(status.scan);
        const tm = status.trade_management || {};
        const lockPips = Number(tm.trailing_sl_lock_pips || 0);
        this.safeSetText('trailingSlState', tm.trailing_sl ? `On @ ${Math.round((tm.trailing_sl_trigger_pct || 0) * 100)}% / +${lockPips.toFixed(1)}p` : 'Off');
        this.safeSetText('trailingTpState', this.formatProfitProtectionState(tm));
        this.renderExecutionFeatureState(tm, status);

        const signals = (payload.signals && payload.signals.recent) ? payload.signals.recent : [];
        this.updateMarketWatch(signals);
        this.updateVision(signals);
        this.updateExecutionTimeline(signals);
        this.renderStrategyBreakdown(signals);
        this.renderSpreadSafety(signals);
        this.renderRejectionSummary(payload.logs?.rejections || [], payload.logs?.diagnostics || []);

        // Optional stats update
        const stats = payload.stats || payload.statistics || {};
        this.safeSetText('winRate', stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '0%');
        this.safeSetText('expectancy', stats.expectancy != null ? stats.expectancy.toFixed(2) : '0');
        this.safeSetText('avgWin', stats.avg_win != null ? `$${stats.avg_win.toFixed(2)}` : '$0');
        this.safeSetText('avgLoss', stats.avg_loss != null ? `$${Math.abs(stats.avg_loss).toFixed(2)}` : '$0');
        this.safeSetText('totalTrades', stats.total_trades || stats.trades || 0);

        if (payload.pending_orders) {
            const pendingBody = document.getElementById('pendingOrdersBody');
            if (pendingBody && payload.pending_orders.length) {
                pendingBody.innerHTML = '';
                payload.pending_orders.forEach((o) => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${o.symbol}</td>
                        <td>${o.action || '-'}</td>
                        <td>${o.entry ? Number(o.entry).toFixed(5) : '-'}</td>
                        <td>${o.sl ? Number(o.sl).toFixed(5) : '-'}</td>
                        <td>${o.tp ? Number(o.tp).toFixed(5) : '-'}</td>
                        <td>${o.status || 'PENDING'}</td>
                    `;
                    pendingBody.appendChild(row);
                });
            } else if (pendingBody) {
                pendingBody.innerHTML = '<tr><td colspan="6" style="text-align:center">No pending orders</td></tr>';
            }
        }

        if (payload.positions) {
            const activeTbody = document.querySelector('#activeTradesBody');
            if (!activeTbody) {
                this.loadPositions();
            } else if (payload.positions.length) {
                activeTbody.innerHTML = '';
                payload.positions.forEach((pos) => {
                    const row = activeTbody.insertRow();
                    const profitColor = pos.profit >= 0 ? '#10b981' : '#ef4444';
                    const rText = pos.r_multiple != null ? `${Number(pos.r_multiple).toFixed(2)}R` : '--';
                    row.innerHTML = `
                        <td>${pos.symbol}</td>
                        <td>${pos.type}</td>
                        <td>${pos.volume}</td>
                        <td>${pos.entry ? Number(pos.entry).toFixed(5) : '-'}</td>
                        <td>${pos.current ? Number(pos.current).toFixed(5) : '-'}</td>
                        <td>${pos.sl ? Number(pos.sl).toFixed(5) : '-'}</td>
                        <td>${pos.tp ? Number(pos.tp).toFixed(5) : '-'}</td>
                        <td>${rText}</td>
                        <td style="color: ${profitColor}">${this.formatMoney(pos.profit)}</td>
                        <td>${this.formatPositionManagement(pos)}</td>
                    `;
                });
            } else {
                activeTbody.innerHTML = '';
                activeTbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No active trades</td></tr>';
            }
        }

        this.updateVisionPanel(payload.metrics || {});
        this.renderValidationLogs(payload.validation_logs || []);
    }

    setupVisionCardClicks() {
        document.querySelectorAll('.vision-card .view-details').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const key = btn.dataset.cardKey;
                this.openDeepDiveModal(key);
            });
        });

        document.querySelectorAll('.vision-card').forEach((card) => {
            card.addEventListener('click', () => {
                const key = card.dataset.visionCard;
                this.openDeepDiveModal(key);
            });
        });

        document.getElementById('closeDeepDiveBtn')?.addEventListener('click', () => {
            this.closeDeepDiveModal();
        });

        // Close drag or outside click for central modal
        document.querySelector('#deepDiveModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'deepDiveModal') {
                this.closeDeepDiveModal();
            }
        });

        document.querySelector('#deepDiveModal .modal-close')?.addEventListener('click', () => {
            this.closeDeepDiveModal();
        });

        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closeDeepDiveModal();
        });
    }

    openDeepDiveModal(cardKey) {
        const titleMap = {
            trendConf: 'Trend Confidence',
            modelScore: 'Model Score',
            emaCheck: 'EMA Check',
            pressure: 'Position Pressure',
            apiExplorer: 'API Explorer',
        };
        const title = titleMap[cardKey] || 'Deep Dive';
        const modalTitle = document.getElementById('deepDiveTitle');
        const subtext = document.getElementById('deepDiveSubtext');
        const logsElem = document.getElementById('validationLogsList');
        const apiElem = document.getElementById('apiEndpointsList');

        if (modalTitle) modalTitle.textContent = title;

        if (cardKey === 'apiExplorer') {
            if (subtext) subtext.textContent = 'Backend API endpoints and docs';
            if (apiElem) {
                apiElem.style.display = 'block';
                this.renderApiEndpoints(this.apiEndpoints || []);
            }
            if (logsElem) logsElem.style.display = 'none';
        } else {
            if (subtext) subtext.textContent = 'Latest validation logs for selected metric';
            if (apiElem) apiElem.style.display = 'none';
            if (logsElem) {
                logsElem.style.display = 'block';
                this.renderValidationLogs(this.validationLogs || []);
            }
        }

        const modal = document.getElementById('deepDiveModal');
        if (modal) modal.classList.add('open');
    }

    closeDeepDiveModal() {
        const modal = document.getElementById('deepDiveModal');
        if (modal) modal.classList.remove('open');
    }

    renderValidationLogs(logs = []) {
        this.validationLogs = logs;
        const list = document.getElementById('validationLogsList');
        if (!list) return;
        list.innerHTML = '';
        if (!logs.length) {
            list.innerHTML = '<li>No validation logs available yet</li>';
            return;
        }
        logs.slice(-20).reverse().forEach((log) => {
            const item = document.createElement('li');
            const statusClass = log.status ? log.status.toLowerCase() : 'pending';
            item.innerHTML = `<div>${new Date(log.timestamp || Date.now()).toLocaleTimeString()}</div> <div><strong>${log.message || log.detail}</strong></div><span class="status-pill ${statusClass}">${(log.status || 'PENDING').toUpperCase()}</span>`;
            list.appendChild(item);
        });
    }

    async loadApiEndpoints() {
        try {
            const res = await fetch('/api/endpoints');
            if (!res.ok) {
                console.warn('API endpoint list failed', res.status);
                return;
            }

            const data = await res.json();
            if (data?.status === 'success' && Array.isArray(data.data)) {
                this.apiEndpoints = data.data;
                const count = data.data.length;
                const counter = document.getElementById('apiEndpointCount');
                if (counter) {
                    counter.textContent = `${count} endpoints`;
                }
            }
        } catch (err) {
            console.error('loadApiEndpoints error', err);
        }
    }

    renderApiEndpoints(endpoints = []) {
        const list = document.getElementById('apiEndpointsList');
        if (!list) return;
        list.innerHTML = '';

        if (!endpoints.length) {
            list.innerHTML = '<li>No endpoints discovered</li>';
            return;
        }

        endpoints.forEach((endpoint) => {
            const card = document.createElement('li');
            const method = (endpoint.method || 'GET').toUpperCase();
            const path = endpoint.path || '/';
            const desc = endpoint.description || '';
            card.innerHTML = `<div><span class="status-pill ${method.toLowerCase()}">${method}</span> <strong>${path}</strong></div><div>${desc}</div>`;
            list.appendChild(card);
        });
    }

    updateVisionPanel(metrics = {}) {
        const trend = metrics.trend_confidence ?? 0;
        const model = metrics.model_score ?? 0;
        const ema = metrics.ema_check ?? 0;
        const pressure = metrics.position_pressure ?? 0;

        this.setConviction('trendConfidenceVal', trend);
        this.setConviction('modelScoreVal', model);
        this.setConviction('emaCheckVal', ema);
        this.setConviction('pressureVal', pressure);

        this.setConvictionBarValue('trendConf', trend);
        this.setConvictionBarValue('modelScore', model);
        this.setConvictionBarValue('emaCheck', ema);
        this.setConvictionBarValue('pressure', pressure);
    }

    setConviction(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        const normalized = Math.max(0, Math.min(1, Number(value)));
        el.textContent = normalized.toFixed(2);
    }

    setConvictionBarValue(cardKey, value) {
        const card = document.querySelector(`[data-vision-card="${cardKey}"] .conviction-bar`);
        if (!card) return;
        const normalized = Math.max(0, Math.min(1, Number(value)));
        card.dataset.value = normalized.toFixed(2);
        card.style.setProperty('--conviction-percent', `${normalized * 100}%`);

        const tone = normalized >= 0.7 ? 'high' : normalized >= 0.4 ? 'medium' : 'low';
        card.classList.remove('conviction-low', 'conviction-medium', 'conviction-high');
        card.classList.add(`conviction-${tone}`);
    }

    showLogicTooltip(target, details, event) {
        if (!this.logicTooltip) return;
        this.logicTooltip.textContent = details;
        this.logicTooltip.classList.add('visible');

        const rect = target.getBoundingClientRect();
        const menu = document.querySelector('.main-content');
        const baseRect = menu ? menu.getBoundingClientRect() : { left: 0, top: 0 };

        this.logicTooltip.style.left = `${rect.left - baseRect.left + rect.width / 2}px`;
        this.logicTooltip.style.top = `${rect.top - baseRect.top - 36}px`;
        this.logicTooltip.style.display = 'block';
    }

    hideLogicTooltip() {
        if (!this.logicTooltip) return;
        this.logicTooltip.classList.remove('visible');
        this.logicTooltip.style.display = 'none';
    }

    navigate(e) {
        e.preventDefault();
        const page = e.currentTarget.dataset.page;

        document.querySelectorAll(".nav-link").forEach((l) =>
            l.classList.remove("active")
        );
        e.currentTarget.classList.add("active");

        document.querySelectorAll(".page").forEach((p) =>
            p.classList.remove("active")
        );
        document.getElementById(page).classList.add("active");

        if (page === "positions") this.loadPositions();
        if (page === "future-trades") this.loadFutureTrades();
        if (page === "logs") this.loadLogs();
        if (page === "settings") this.loadSettings();
    }

    async start() {
        try {
            const payload = {
                symbols: document.getElementById("symbols")?.value,
                volume: document.getElementById("volume")?.value,
            };

            const res = await fetch(`${this.apiBase}/bot/start`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status === "success") {
                alert("Bot started!");
                this.updateDashboard();
            } else {
                alert("Error: " + (data.message || "Failed to start"));
            }
        } catch (e) {
            alert("Error: " + e.message);
        }
    }

    async stop() {
        try {
            const res = await fetch(`${this.apiBase}/bot/stop`, {
                method: "POST",
            });
            const data = await res.json();
            if (data.status === "success") {
                alert("Bot stopped!");
                this.updateDashboard();
            }
        } catch (e) {
            alert("Error: " + e.message);
        }
    }

    async updateDashboard() {
        try {
            const statusRes = await fetch(`${this.apiBase}/bot/status`);
            const statusData = await statusRes.json();
            const logsData = { data: statusData.logs || {} };
            const statsData = { data: statusData.stats || {} };

            if (statusData) {
                const d = statusData;
                this.safeSetText('botStatus', d.running ? 'Online' : 'Offline');
                const statusIcon = document.getElementById('statusIcon');
                if (statusIcon) statusIcon.className = d.running ? 'fas fa-circle online' : 'fas fa-circle';
                this.safeSetText('connected', d.connected ? 'Connected' : 'Disconnected');
                const account = d.account || {};
                const accountMode = account.trade_mode_label || '--';
                const accountLogin = account.login || 'current';
                const accountServer = account.server || 'attached terminal';
                const leverage = account.leverage ? ` 1:${account.leverage}` : '';
                const profile = d.account_profile || {};
                const profileText = profile.name ? ` | ${String(profile.name).toUpperCase()} profile` : '';
                this.safeSetText('mt5LiveAccount', `${accountMode} ${accountLogin} @ ${accountServer}${leverage}${profileText}`);
                this.safeSetText('balance', `$${(d.balance || 0).toFixed(2)}`);
                this.safeSetText('equity', `$${(d.equity || 0).toFixed(2)}`);
                this.safeSetText('margin', `$${(d.free_margin || 0).toFixed(2)}`);
                this.setMoney('dailyProfit', d.daily_profit);
                this.setMoney('floatingProfit', d.floating_profit);
                this.setMoney('realizedProfit', d.realized_profit);
                this.setMoney('netProfit', d.net_profit);
                this.setMoney('drawdown', d.floating_drawdown);
                this.setMoney('openRisk', d.current_open_risk);
                this.setMoney('maxOpenRisk', d.max_open_risk);
                const openRiskPct = Number(d.open_risk_pct);
                const maxOpenRiskPct = Number(d.max_open_risk_pct);
                const riskUsage = Number.isFinite(openRiskPct) && Number.isFinite(maxOpenRiskPct) && maxOpenRiskPct > 0
                    ? Math.min(100, Math.max(0, (openRiskPct / maxOpenRiskPct) * 100))
                    : 0;
                const openRiskMeter = document.getElementById('openRiskMeter');
                if (openRiskMeter) {
                    openRiskMeter.style.width = `${riskUsage}%`;
                    openRiskMeter.dataset.state = riskUsage >= 90 ? 'danger' : riskUsage >= 70 ? 'warning' : 'ok';
                }
                this.safeSetText('openRiskPct', Number.isFinite(openRiskPct) ? `${(openRiskPct * 100).toFixed(1)}% used` : '0.0% used');
                this.safeSetText('openRiskState', d.current_open_risk > 0 ? `${riskUsage.toFixed(0)}% of risk cap` : 'No active stop-risk');
                const sizingMode = String(d.position_sizing_mode || 'fixed').replace('_', ' ');
                this.safeSetText('sizingMode', sizingMode.charAt(0).toUpperCase() + sizingMode.slice(1));
                this.safeSetText('activeTrades', d.active_trades || 0);
                this.renderScanStatus(d.scan);
                const tm = d.trade_management || {};
                const lockPips = Number(tm.trailing_sl_lock_pips || 0);
                this.safeSetText('trailingSlState', tm.trailing_sl ? `On @ ${Math.round((tm.trailing_sl_trigger_pct || 0) * 100)}% / +${lockPips.toFixed(1)}p` : 'Off');
                this.safeSetText('trailingTpState', this.formatProfitProtectionState(tm));
                this.renderExecutionFeatureState(tm, d);

                const marginLevel = d.margin_level || 0;
                this.safeSetText('marginLevel', `${marginLevel.toFixed(2)}%`);

                const floatingPnl = d.floating_profit != null ? d.floating_profit : (d.equity || 0) - (d.balance || 0);
                this.setMoney('floatingPnl', floatingPnl);

                const botScore = d.bot_score || {};
                const scoreValue = Number(botScore.score);
                const botIQ = Number.isFinite(scoreValue)
                    ? Math.min(100, Math.max(0, scoreValue))
                    : Math.min(100, Math.max(0, ((d.connected ? 1 : 0) * 0.6 + (d.running ? 1 : 0) * 0.4) * 100));
                this.safeSetText('botIQ', `${botIQ.toFixed(0)}%`);
                this.safeSetText('botScoreGrade', botScore.grade ? `${botScore.grade} - ${botScore.label || 'Ready'}` : '--');
                const logicFill = document.getElementById('logicHealthFill');
                if (logicFill) logicFill.style.width = `${botIQ}%`;

                if (d.max_exposure != null) {
                    this.safeSetText('riskMaxExposure', `${Math.round(d.max_exposure * 100)}%`);
                }
                if (d.daily_profit != null) {
                    this.safeSetText('dailyProfitCap', `${(d.daily_profit * 100).toFixed(2)}%`);
                }

                if (statsData && statsData.data) {
                    const s = statsData.data;
                    this.safeSetText('winRate', s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : '0%');
                    this.safeSetText('expectancy', s.expectancy != null ? s.expectancy.toFixed(2) : '0');
                    this.safeSetText('avgWin', s.avg_win != null ? `$${s.avg_win.toFixed(2)}` : '$0');
                    this.safeSetText('avgLoss', s.avg_loss != null ? `$${Math.abs(s.avg_loss).toFixed(2)}` : '$0');
                    this.safeSetText('totalTrades', s.total_trades || s.trades || 0);
                }

                const startBtn = document.getElementById('startBtn');
                const stopBtn = document.getElementById('stopBtn');
                if (startBtn) startBtn.disabled = d.running;
                if (stopBtn) stopBtn.disabled = !d.running;

                this.symbols = d.symbols || [];
            }

            if (logsData && logsData.data) {
                const scoredSignals = [
                    ...(logsData.data.signals || []),
                    ...(logsData.data.future_trades || [])
                ];
                this.renderStrategyBreakdown(scoredSignals);
                this.renderSpreadSafety(scoredSignals);
                this.renderRejectionSummary(logsData.data.rejections || [], logsData.data.diagnostics || []);
            }

            await this.loadPendingOrders(statusData.pending_orders || []);
            await this.loadPositions(statusData.positions || []);
        } catch (e) {
            console.error("Dashboard update error:", e);
        }
    }

    async loadPositions(cachedPositions = null) {
        try {
            const tbody = document.querySelector("#positionsTable tbody");
            const activeTbody = document.querySelector("#activeTradesBody");
            if (!tbody) {
                console.warn('loadPositions: positions table not found');
                return;
            }

            tbody.innerHTML = "";
            if (activeTbody) activeTbody.innerHTML = "";

            let positions = Array.isArray(cachedPositions) ? cachedPositions : null;
            if (!positions) {
                const res = await fetch(`${this.apiBase}/positions`);
                if (!res.ok) {
                    console.warn('loadPositions: non-OK response', res.status);
                    this.markNoPositions();
                    return;
                }
                const data = await res.json();
                positions = (data && data.data) ? data.data : [];
            }
            this.safeSetText('activeTrades', positions.length);
            if (positions.length > 0) {
                positions.forEach((pos) => {
                    const entry = Number(pos.entry || 0);
                    const current = Number(pos.current || 0);
                    const profit = Number(pos.profit || 0);
                    const profitColor = profit >= 0 ? "#10b981" : "#ef4444";
                    const state = pos.trade_state || {};
                    const rText = pos.r_multiple != null ? `${Number(pos.r_multiple).toFixed(2)}R` : '--';
                    const management = this.formatPositionManagement(pos);

                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td>${pos.symbol || '-'}</td>
                        <td>${pos.type || '-'}</td>
                        <td>${pos.volume || '-'}</td>
                        <td>${entry.toFixed(5)}</td>
                        <td>${current.toFixed(5)}</td>
                        <td>${pos.sl ? Number(pos.sl).toFixed(5) : '-'}</td>
                        <td>${pos.tp ? Number(pos.tp).toFixed(5) : '-'}</td>
                        <td>${rText}</td>
                        <td style="color: ${profitColor}">${this.formatMoney(profit)}</td>
                        <td>${management}</td>
                    `;

                    if (activeTbody) {
                        const activeRow = activeTbody.insertRow();
                        activeRow.innerHTML = row.innerHTML;
                    }
                });
            } else {
                this.markNoPositions();
            }
        } catch (e) {
            console.error("Positions error:", e);
            this.markNoPositions();
        }
    }

    formatPositionManagement(pos = {}) {
        const state = pos.trade_state || {};
        const chips = [];
        chips.push(`<span class="setup-chip ${state.status === 'ACTIVE' ? 'pass' : ''}">${state.status || 'EXTERNAL'}</span>`);
        if (state.trade_horizon?.type) chips.push(`<span class="setup-chip">${state.trade_horizon.type}</span>`);
        if (state.symbol_profile) chips.push(`<span class="setup-chip">Sym ${state.symbol_profile}</span>`);
        if (state.horizon_profile) chips.push(`<span class="setup-chip">Plan ${state.horizon_profile}</span>`);
        if (state.management_profile) chips.push(`<span class="setup-chip">Mgmt ${state.management_profile}</span>`);
        if (state.partial_tp_taken) chips.push('<span class="setup-chip pass">Partial TP</span>');
        if (state.partial_runner_tp) chips.push(`<span class="setup-chip pass">Runner TP ${Number(state.partial_runner_tp).toFixed(5)}</span>`);
        if (state.opposing_signal_exit) chips.push('<span class="setup-chip warn">Opp exit queued</span>');
        if (state.reverse_exit_done) chips.push('<span class="setup-chip pass">Reverse exit</span>');
        if (state.news_ladder_count) chips.push(`<span class="setup-chip pass">News adds ${state.news_ladder_count}</span>`);
        if (state.max_favorable_r != null) chips.push(`<span class="setup-chip">MFE ${Number(state.max_favorable_r).toFixed(2)}R</span>`);
        return `<div class="position-management">${chips.join('')}</div>`;
    }

    markNoPositions() {
        const tbody = document.querySelector("#positionsTable tbody");
        const activeTbody = document.querySelector("#activeTradesBody");
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No positions</td></tr>';
        }
        if (activeTbody) {
            activeTbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No active trades</td></tr>';
        }
    }

    async loadPendingOrders(cachedOrders = null) {
        try {
            const tbody = document.getElementById('pendingOrdersBody');
            if (!tbody) return;
            tbody.innerHTML = '';

            let orders = Array.isArray(cachedOrders) ? cachedOrders : null;
            if (!orders) {
                const res = await fetch(`${this.apiBase}/pending-orders`);
                const data = await res.json();
                orders = data.data || [];
            }
            if (orders.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">No pending orders</td></tr>';
                return;
            }

            orders.forEach((o) => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${o.symbol}</td>
                    <td>${o.action || '-'}</td>
                    <td>${o.entry ? Number(o.entry).toFixed(5) : '-'}</td>
                    <td>${o.sl ? Number(o.sl).toFixed(5) : '-'}</td>
                    <td>${o.tp ? Number(o.tp).toFixed(5) : '-'}</td>
                    <td>${o.status || 'PENDING'}</td>
                `;
                tbody.appendChild(row);
            });
        } catch (e) {
            console.error('Pending orders error:', e);
        }
    }

    async loadLogs() {
        try {
            const res = await fetch(`${this.apiBase}/logs`);
            const data = await res.json();
            const container = document.getElementById("logsContainer");
            container.innerHTML = "";

            const rejections = data.data ? data.data.rejections || [] : [];
            const trades = data.data ? data.data.trades || [] : [];
            const signals = data.data ? data.data.signals || [] : [];
            const futureTrades = data.data ? data.data.future_trades || [] : [];
            const diagnostics = data.data ? data.data.diagnostics || [] : [];

            if (rejections.length === 0 && trades.length === 0 && signals.length === 0 && futureTrades.length === 0 && diagnostics.length === 0) {
                container.innerHTML = "<p>No logs</p>";
                return;
            }

            const groups = [
                {
                    title: 'Trades',
                    items: trades,
                    label: (log) => `${this.humanEventName(log.event || 'Trade')} - ${log.symbol || ''} ${log.trade_style ? '(' + log.trade_style + ')' : ''}`,
                    meta: (log) => log.timestamp ? new Date(log.timestamp).toLocaleString() : '',
                },
                {
                    title: 'Signals',
                    items: signals,
                    label: (signal) => `${signal.symbol || 'N/A'} - ${signal.type || signal.nature || 'Signal'}`,
                    meta: (signal) => signal.timestamp ? new Date(signal.timestamp).toLocaleString() : '',
                },
                {
                    title: 'Future Trades',
                    items: futureTrades,
                    label: (item) => `${item.symbol || 'N/A'} - ${item.type || item.nature || item.setup_name || 'Watchlist'}`,
                    meta: (item) => item.phase || item.action_needed || '',
                },
                {
                    title: 'Rejections',
                    items: rejections,
                    label: (log) => `Rejected - ${log.symbol || ''}`,
                    meta: (log) => log.reason || '',
                },
                {
                    title: 'Diagnostics',
                    items: diagnostics,
                    label: (log) => `${this.humanEventName(log.event || 'Diagnostic')} - ${log.symbol || 'SYSTEM'}`,
                    meta: (log) => log.reason || '',
                },
            ];

            groups.forEach((group) => {
                if (!group.items.length) return;
                const section = document.createElement('section');
                section.className = 'log-section';
                section.innerHTML = `<h4>${this.escapeHtml(group.title)} <span>${group.items.length}</span></h4>`;
                const topScore = ['Signals', 'Future Trades'].includes(group.title)
                    ? Math.max(...group.items.map((item) => this.getSignalQualityScore(item)))
                    : null;
                group.items.slice().reverse().forEach((item) => {
                    section.appendChild(this.createLogAccordionItem(item, group.label(item), group.meta(item), {topScore}));
                });
                container.appendChild(section);
            });
        } catch (e) {
            console.error("Logs error:", e);
        }
    }

    async loadSignals() {
        try {
            const res = await fetch(`${this.apiBase}/signals`);
            const data = await res.json();
            const container = document.getElementById("signalsTable");
            container.innerHTML = "";

            const signals = (data.data && data.data.recent) ? data.data.recent : (data.data ? data.data.recent || [] : []);
            if (signals && signals.length > 0) {
                const table = document.createElement('table');
                table.className = 'table signals-table';
                table.innerHTML = `
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Signal</th>
                            <th>Entry</th>
                            <th>Conviction</th>
                            <th>Style</th>
                            <th>Status</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                `;
                container.appendChild(table);
                const tbody = table.querySelector('tbody');

                const topScore = Math.max(...signals.map((sig) => this.getSignalQualityScore(sig)));
                signals.forEach((sig, index) => {
                    const row = tbody.insertRow();
                    const conviction = sig.conviction || 0;
                    const convictionClass = conviction >= 0.6 ? 'high' : conviction >= 0.3 ? 'medium' : 'low';
                    const best = this.isBestSignal(sig, topScore);
                    if (best) row.classList.add('best-signal-row');
                    row.innerHTML = `
                        <td>${sig.symbol} ${best ? this.getBestSignalBadge({...sig, best_signal: true}) : ''}</td>
                        <td>${sig.nature || sig.type || ''}</td>
                        <td>${sig.entry ? sig.entry.toFixed(5) : ''}</td>
                        <td><span class="conviction-bar ${convictionClass}" style="width: ${conviction * 100}%"></span> ${conviction.toFixed(3)}</td>
                        <td>${sig.trade_style || ''}</td>
                        <td>${sig.status || ''}</td>
                        <td>${this.buildExecuteButtonHtml(sig)}</td>
                    `;
                    row.style.cursor = 'pointer';
                    row.addEventListener('click', () => this.showSignalDetails(sig));
                    row.querySelector('.signal-execute-btn')?.addEventListener('click', (event) => {
                        event.stopPropagation();
                        this.executeSignal(sig, index, 'recent');
                    });
                });

                // update hunter & vision modules
                this.updateMarketWatch(signals);
                this.updateVision(signals);
                this.updateExecutionTimeline(signals);
            } else {
                container.innerHTML = '<div class="empty-state"><p>No signals</p></div>';
                this.updateMarketWatch([]);
                this.updateVision([]);
                this.updateExecutionTimeline([]);
            }
        } catch (e) {
            console.error('Signals error:', e);
        }
    }

    async executeSignal(signal, index, source = 'recent') {
        const symbol = signal?.symbol || 'this symbol';
        const action = this.inferSignalAction(signal);
        const confirmed = window.confirm(`Execute ${action || 'signal'} for ${symbol} now?`);
        if (!confirmed) return;

        try {
            const res = await fetch(`${this.apiBase}/signals/execute`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    source,
                    index,
                    symbol: signal?.symbol,
                    action,
                    signal,
                    route: 'market'
                })
            });
            const data = await res.json();
            if (data.status === 'success') {
                this.showNotification(data.message || `Manual execution sent for ${symbol}`, 'success');
                await this.updateDashboard();
                await this.loadSignals();
            } else {
                this.showNotification(data.message || 'Manual execution failed', 'error');
            }
        } catch (e) {
            console.error('Manual signal execution error:', e);
            this.showNotification('Manual execution request failed', 'error');
        }
    }

    inferSignalAction(signal = {}) {
        const raw = String(
            signal.action || signal.direction || signal.type || signal.nature || signal.signal || ''
        ).toUpperCase();
        if (raw.includes('SELL') || raw.includes('BEAR') || raw.includes('SHORT')) return 'SELL';
        if (raw.includes('BUY') || raw.includes('BULL') || raw.includes('LONG')) return 'BUY';
        return raw;
    }

    isExecutableSignal(signal = {}) {
        if (!signal || !signal.symbol) return false;
        return ['BUY', 'SELL'].includes(this.inferSignalAction(signal));
    }

    buildExecuteButtonHtml(signal, extraClass = '') {
        if (!this.isExecutableSignal(signal)) return '';
        return `
            <button class="btn-small btn-place signal-execute-btn ${extraClass}" type="button" title="Execute this signal now">
                <i class="fas fa-play"></i> Execute
            </button>
        `;
    }

    updateMarketWatch(signals) {
        const body = document.getElementById('marketWatchBody');
        if (!body) return;
        body.innerHTML = '';

        if (!signals || signals.length === 0) {
            body.innerHTML = '<tr><td colspan="5" style="text-align:center">No market signals available</td></tr>';
            return;
        }

        const byConvictions = signals
            .slice()
            .sort((a, b) => ((b.setup_score?.score || b.conviction || 0) - (a.setup_score?.score || a.conviction || 0)));

        byConvictions.slice(0, 15).forEach((sig) => {
            const row = document.createElement('tr');
            const c = sig.conviction || 0;
            const setup = sig.setup_score?.score || 0;
            const convictionClass = c >= 0.6 ? 'high' : c >= 0.3 ? 'medium' : 'low';
            const price = sig.current_price || sig.entry || 0;
            const direction = (sig.nature || sig.type || '').toUpperCase();

            row.innerHTML = `
                <td>${sig.symbol || '-'}</td>
                <td>${price ? price.toFixed(5) : '-'}</td>
                <td><span class="conviction-bar ${convictionClass}" style="width: ${Math.min(100, Math.max(0, Math.max(c, setup)*100))}%"></span> ${Math.max(c, setup).toFixed(2)}</td>
                <td>${direction}${sig.early_entry ? ' | EARLY' : ''}</td>
                <td>${this.buildExecuteButtonHtml(sig, 'market-watch-execute-btn')}</td>
            `;
            row.querySelector('.signal-execute-btn')?.addEventListener('click', (event) => {
                event.stopPropagation();
                this.executeSignal(sig, null, 'payload');
            });
            body.appendChild(row);
        });
    }

    updateVision(signals) {
        const top = (signals || []).slice().sort((a, b) => ((b.setup_score?.score || b.conviction || 0) - (a.setup_score?.score || a.conviction || 0)))[0];
        this.currentDecisionSignal = top || null;
        const bestConv = top ? (top.conviction || 0) : 0;
        const setupScore = top ? (top.setup_score?.score || 0) : 0;
        const price = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(5) : '--';
        const direction = top ? String(top.type || top.nature || top.direction || 'Unknown').toUpperCase() : '--';
        const horizon = top?.trade_horizon || {};
        const horizonType = horizon.type || 'INTRADAY';
        const holdTime = horizon.hold_time ? ` (${horizon.hold_time})` : '';
        const grade = top ? this.getSetupGrade(top) : '--';
        const archetype = top?.setup_score?.archetype || top?.setup_score?.summary || top?.strategy || top?.nature || 'Waiting for cleaner structure';
        const decision = top ? this.getDecisionLabel(top, bestConv, setupScore) : 'WAIT';
        const rr = top ? this.calculateRiskReward(top) : null;
        const spread = top?.spread_safety || top?.spread || top?.spread_pips;
        const spreadLabel = typeof spread === 'object'
            ? `${spread.safe === false ? 'High' : 'OK'}${spread.spread_pips != null ? ` / ${Number(spread.spread_pips).toFixed(1)}p` : ''}`
            : (Number.isFinite(Number(spread)) ? `${Number(spread).toFixed(1)}p` : '--');

        this.safeSetText('topSymbol', top ? top.symbol : 'None');
        this.safeSetText('bestConviction', `${(bestConv * 100).toFixed(1)}%`);
        this.safeSetText('nextTradeAction', top ? `${direction} @ ${price(top.entry)}` : 'None');
        this.safeSetText('hotZone', top ? price(top.entry) : 'None');
        this.safeSetText('decisionSymbol', top ? top.symbol : 'No setup');
        this.safeSetText('decisionArchetype', archetype);
        this.safeSetText('decisionTradeType', top ? `${horizonType}${holdTime}` : '--');
        this.safeSetText('decisionDirection', direction);
        this.safeSetText('decisionGrade', grade);
        this.safeSetText('decisionEntry', top ? price(top.entry) : '--');
        this.safeSetText('decisionSl', top ? price(top.stop_loss || top.sl) : '--');
        this.safeSetText('decisionTp', top ? price(top.take_profit || top.tp) : '--');
        this.safeSetText('decisionRr', rr ? `1:${rr.toFixed(2)}` : '--');
        this.safeSetText('decisionSpread', spreadLabel);
        this.safeSetText('decisionReason', this.buildDecisionReason(top, decision, grade));
        const methodStrip = document.getElementById('decisionMethodStrip');
        if (methodStrip) {
            methodStrip.innerHTML = top
                ? this.renderExecutionMethodChips(top)
                : '<span class="setup-chip">Method pending</span><span class="setup-chip">MTF pending</span>';
        }

        const badge = document.getElementById('decisionBadge');
        if (badge) {
            badge.textContent = decision;
            badge.className = `decision-badge ${decision.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
        }
        const executeBtn = document.getElementById('decisionExecuteBtn');
        if (executeBtn) {
            executeBtn.disabled = !this.isExecutableSignal(top);
            executeBtn.title = top ? 'Manually execute this best setup now' : 'No executable setup available';
        }

        if (top) {
            this.safeSetText('visionSymbol', top.symbol);
            this.safeSetText('visionType', direction);
            this.safeSetText('visionEntry', price(top.entry));
            this.safeSetText('actionStatus', decision);
            const action = document.getElementById('actionStatus');
            if (action) action.style.background = bestConv >= 0.6 ? '#22c55e' : bestConv >= 0.35 ? '#f59e0b' : '#ef4444';
        } else {
            this.safeSetText('visionSymbol', '--');
            this.safeSetText('visionType', '--');
            this.safeSetText('visionEntry', '--');
            this.safeSetText('actionStatus', 'NO SIGNAL');
            const action = document.getElementById('actionStatus');
            if (action) action.style.background = '#64748b';
        }

        this.renderDecisionComponents(top);
        this.renderSetupChecklist(top);
        this.setConviction('setupScoreVal', setupScore);
        this.setConvictionBarValue('setupScore', setupScore);
        this.setConvictionBarValue('modelScore', bestConv);
        this.setVisionGauge(Math.max(bestConv, setupScore));
        this.renderVisionChart(top);
        this.loadChartVisuals(top);
    }

    getDecisionLabel(signal, conviction, setupScore) {
        const status = String(signal?.status || signal?.decision || '').toUpperCase();
        if (status.includes('TRADE') || status.includes('APPROVED') || signal?.approved) return 'READY';
        if (status.includes('WAIT') || status.includes('REJECT')) return 'WAIT';
        if (setupScore >= 0.58 && conviction >= 0.45) return 'READY';
        if (setupScore >= 0.50 || conviction >= 0.40 || signal?.early_entry) return 'WATCH';
        return 'WAIT';
    }

    calculateRiskReward(signal) {
        const entry = Number(signal?.entry || signal?.current_price);
        const sl = Number(signal?.stop_loss || signal?.sl);
        const tp = Number(signal?.take_profit || signal?.tp);
        if (![entry, sl, tp].every(Number.isFinite)) return null;
        const risk = Math.abs(entry - sl);
        const reward = Math.abs(tp - entry);
        if (!risk || !reward) return null;
        return reward / risk;
    }

    renderDecisionComponents(signal) {
        const passedBox = document.getElementById('decisionPassed');
        const missingBox = document.getElementById('decisionMissing');
        if (!passedBox || !missingBox) return;

        const components = signal?.setup_score?.components || [];
        const passed = components.filter((item) => item.passed);
        const missing = components.filter((item) => !item.passed);

        const render = (items, fallback, tone = '') => {
            if (!items.length) return `<span class="setup-chip ${tone}">${fallback}</span>`;
            return items.slice(0, 7).map((item) => `
                <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${this.escapeHtml(item.detail || '')}">
                    ${item.passed ? 'OK' : 'Missing'} ${this.escapeHtml(item.label || 'Component')}
                </span>
            `).join('');
        };

        passedBox.innerHTML = render(passed, signal ? 'No confirmed components yet' : 'No confirmations yet');
        missingBox.innerHTML = render(missing, signal ? 'No major blocker detected' : 'Waiting for signal', signal ? 'pass' : 'fail');
    }

    buildDecisionReason(signal, decision, grade) {
        if (!signal) return 'No ranked setup yet. Waiting for structure.';
        const components = signal?.setup_score?.components || [];
        const missing = components.filter((item) => !item.passed).slice(0, 2).map((item) => item.label);
        const archetype = signal?.setup_score?.archetype || signal?.setup_score?.summary || signal?.nature || 'setup';
        if (decision === 'READY') return `READY: Grade ${grade} ${archetype}. Execution filters are aligned.`;
        if (decision === 'WATCH') {
            return `WATCH: Grade ${grade} ${archetype}. ${missing.length ? `Needs ${missing.join(' and ')}.` : 'Waiting for stronger confirmation.'}`;
        }
        return `WAIT: Grade ${grade} ${archetype}. ${missing.length ? `Missing ${missing.join(' and ')}.` : 'Quality is below execution threshold.'}`;
    }

    renderSetupChecklist(signal) {
        const box = document.getElementById('setupChecklist');
        if (!box) return;
        const components = signal?.setup_score?.components || [];
        if (!components.length) {
            box.innerHTML = '<span>No setup components yet</span>';
            return;
        }
        box.innerHTML = components.slice(0, 6).map((item) => `
            <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                ${item.passed ? '✓' : '×'} ${item.label}
            </span>
        `).join('');
    }

    getSetupScore(signal) {
        return Number(signal?.setup_score?.score || signal?.confluence_score || signal?.conviction || 0);
    }

    getSetupGrade(signal) {
        const grade = signal?.setup_score?.grade;
        if (grade) return grade;
        const score = this.getSetupScore(signal);
        if (score >= 0.78) return 'A';
        if (score >= 0.65) return 'B';
        if (score >= 0.50) return 'C';
        return 'D';
    }

    renderStrategyBreakdown(signals = []) {
        const grid = document.getElementById('strategyBreakdownGrid');
        if (!grid) return;

        const scored = (signals || [])
            .filter((signal) => signal && (signal.setup_score || signal.confluence_score != null || signal.conviction != null))
            .slice()
            .sort((a, b) => this.getSetupScore(b) - this.getSetupScore(a))
            .slice(0, 6);

        if (!scored.length) {
            grid.innerHTML = '<div class="empty-state-compact">Waiting for scored setups...</div>';
            return;
        }

        grid.innerHTML = scored.map((signal, index) => {
            const score = this.getSetupScore(signal);
            const grade = this.getSetupGrade(signal);
            const components = signal.setup_score?.components || [];
            const passed = components.filter((item) => item.passed).length;
            const componentHtml = components.slice(0, 5).map((item) => `
                <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                    ${item.passed ? '✓' : '×'} ${item.label}
                </span>
            `).join('');

            return `
                <div class="strategy-breakdown-card" data-signal-index="${index}">
                    <div class="strategy-breakdown-top">
                        <strong>${signal.symbol || '-'}</strong>
                        <span class="setup-grade grade-${String(grade).toLowerCase()}">${grade}</span>
                    </div>
                    <div class="strategy-score-line">
                        <div class="strategy-score-bar"><span style="width:${Math.min(100, Math.max(0, score * 100))}%"></span></div>
                        <b>${(score * 100).toFixed(0)}%</b>
                    </div>
                    <div class="execution-method-row">${this.renderExecutionMethodChips(signal)}</div>
                    <p>${signal.setup_score?.summary || signal.nature || signal.type || 'Composite setup'}</p>
                    <small>${passed}/${components.length || 8} components passed</small>
                    <div class="setup-checklist">${componentHtml || '<span>No component detail</span>'}</div>
                    <div class="signal-card-actions">
                        <button class="btn-small btn-reset signal-details-btn" type="button">Details</button>
                        ${this.buildExecuteButtonHtml(signal, 'strategy-execute-btn')}
                    </div>
                </div>
            `;
        }).join('');

        grid.querySelectorAll('.strategy-breakdown-card').forEach((card) => {
            const signal = scored[Number(card.dataset.signalIndex)];
            card.querySelector('.signal-details-btn')?.addEventListener('click', (event) => {
                event.stopPropagation();
                this.showSignalDetails(signal);
            });
            card.querySelector('.signal-execute-btn')?.addEventListener('click', (event) => {
                event.stopPropagation();
                this.executeSignal(signal, null, 'payload');
            });
            card.addEventListener('click', () => this.showSignalDetails(signal));
        });
    }

    renderSpreadSafety(signals = []) {
        const grid = document.getElementById('spreadSafetyGrid');
        if (!grid) return;

        const items = (signals || [])
            .filter((signal) => signal && signal.symbol)
            .reduce((acc, signal) => {
                acc[signal.symbol] = signal;
                return acc;
            }, {});

        const rows = Object.values(items).slice(0, 8);
        if (!rows.length) {
            grid.innerHTML = '<div class="empty-state-compact">No spread data yet</div>';
            return;
        }

        grid.innerHTML = rows.map((signal) => {
            const spread = signal.spread_safety || signal.setup_score?.spread || {};
            const safe = spread.safe !== false;
            const value = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)} pips` : 'n/a';
            return `
                <div class="spread-safety-row ${safe ? 'safe' : 'unsafe'}">
                    <strong>${signal.symbol}</strong>
                    <span>${value}</span>
                    <em>${safe ? 'Safe' : 'Avoid'}</em>
                </div>
            `;
        }).join('');
    }

    renderRejectionSummary(rejections = [], diagnostics = []) {
        const grid = document.getElementById('rejectionSummaryGrid');
        if (!grid) return;

        const grouped = {};
        const source = (rejections && rejections.length) ? rejections : diagnostics;
        (source || []).slice(-80).forEach((item) => {
            const symbol = item.symbol || 'GLOBAL';
            if (!grouped[symbol]) grouped[symbol] = [];
            grouped[symbol].push(item.reason || item.message || 'Rejected');
        });

        const rows = Object.entries(grouped).slice(-8).reverse();
        if (!rows.length) {
            grid.innerHTML = '<div class="empty-state-compact">No recent rejection or diagnostic notes</div>';
            return;
        }

        grid.innerHTML = rows.map(([symbol, reasons]) => {
            const reason = reasons[reasons.length - 1] || 'Rejected';
            return `
                <div class="rejection-summary-row">
                    <strong>${symbol}</strong>
                    <span>${reasons.length}x</span>
                    <p>${reason}</p>
                </div>
            `;
        }).join('');
    }

    setVisionGauge(conviction) {
        const ring = document.getElementById('visionRingFill');
        const level = Math.min(1, Math.max(0, conviction || 0));
        const offset = 314 - (314 * level);
        if (ring) ring.style.strokeDashoffset = offset;

        const text = document.getElementById('visionRingValue');
        if (text) text.textContent = `${(level * 100).toFixed(0)}%`;

        if (level >= 0.6) {
            ring.style.stroke = '#10b981';
        } else if (level >= 0.35) {
            ring.style.stroke = '#f59e0b';
        } else {
            ring.style.stroke = '#ef4444';
        }
    }

    renderVisionChart(signal) {
        if (!this.visionChart || !this.visionSeries) return;

        if (!signal) {
            this.visionSeries.setData([
                { time: Math.floor(Date.now() / 1000) - 3, value: 0.0 },
            ]);
            if (this.visionTrendSeries) this.visionTrendSeries.setData([]);
            this.clearVisionPriceLines();
            return;
        }

        const entry = Number(signal.entry || 0);
        const tp = Number(signal.tp || entry + entry * 0.0015);
        const sl = Number(signal.sl || entry - entry * 0.0015);

        const now = Math.floor(Date.now() / 1000);
        const chartData = [
            { time: now - 30, value: sl },
            { time: now - 20, value: entry },
            { time: now - 10, value: (entry + tp) / 2 },
            { time: now, value: tp },
        ];

        this.visionSeries.setData(chartData);

        this.visionSeries.setMarkers([
            { time: now - 30, position: 'below', color: '#ef4444', shape: 'circle', text: `SL ${sl.toFixed(5)}` },
            { time: now - 20, position: 'below', color: '#60a5fa', shape: 'circle', text: `ENTRY ${entry.toFixed(5)}` },
            { time: now, position: 'above', color: '#10b981', shape: 'circle', text: `TP ${tp.toFixed(5)}` },
        ]);

        this.drawTradePriceLines(entry, sl, tp);
    }

    clearVisionPriceLines() {
        if (!this.visionSeries || !this.visionPriceLines) return;
        this.visionPriceLines.forEach((line) => {
            try {
                this.visionSeries.removePriceLine(line);
            } catch (e) {
                // Lightweight Charts can throw if a line was already removed.
            }
        });
        this.visionPriceLines = [];
    }

    drawTradePriceLines(entry, sl, tp) {
        if (!this.visionSeries || typeof this.visionSeries.createPriceLine !== 'function') return;
        this.clearVisionPriceLines();
        [
            { price: entry, color: '#60a5fa', title: 'ENTRY' },
            { price: sl, color: '#ef4444', title: 'SL' },
            { price: tp, color: '#22c55e', title: 'TP' },
        ].forEach((line) => {
            if (!Number.isFinite(line.price)) return;
            this.visionPriceLines.push(this.visionSeries.createPriceLine({
                price: line.price,
                color: line.color,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: line.title,
            }));
        });
    }

    async loadChartVisuals(signal) {
        if (!signal?.symbol || !this.visionSeries) return;
        const symbol = signal.symbol;
        if (this.lastVisualSymbol === symbol && this.lastVisualLoadAt && Date.now() - this.lastVisualLoadAt < 10000) return;
        this.lastVisualSymbol = symbol;
        this.lastVisualLoadAt = Date.now();

        try {
            const res = await fetch(`${this.apiBase}/chart-visuals/${encodeURIComponent(symbol)}`);
            if (!res.ok) return;
            const payload = await res.json();
            const visuals = payload.data || {};
            if (Array.isArray(visuals.candles) && visuals.candles.length) {
                this.visionSeries.setData(visuals.candles);
            }
            if (this.visionTrendSeries) {
                const points = visuals.trendline?.points || [];
                this.visionTrendSeries.setData(points.length >= 2 ? points : []);
            }
            if (this.visionSeries && typeof this.visionSeries.createPriceLine === 'function') {
                const entry = Number(signal.entry || 0);
                const sl = Number(signal.sl || signal.stop_loss || 0);
                const tp = Number(signal.tp || signal.take_profit || 0);
                this.drawTradePriceLines(entry, sl, tp);
                (visuals.levels || []).forEach((level) => {
                    const value = Number(level.value);
                    if (!Number.isFinite(value)) return;
                    this.visionPriceLines.push(this.visionSeries.createPriceLine({
                        price: value,
                        color: level.color || '#94a3b8',
                        lineWidth: 1,
                        lineStyle: 1,
                        axisLabelVisible: true,
                        title: level.label || 'Level',
                    }));
                });
            }
        } catch (e) {
            console.warn('Chart visuals unavailable:', e);
        }
    }

    buildLogicPills(message) {
        if (!message) return [];
        const pills = [];
        const tags = [
            { key: 'EMA', label: 'EMA ✅', detail: 'EMA filter status is included in this logic step.' },
            { key: 'FVG', label: 'FVG 🎯', detail: 'Convening Fair Value Gap pattern detection logic.' },
            { key: 'VOL', label: 'VOL 🔥', detail: 'Volume confirmation rule is referenced.' },
            { key: 'PENDING', label: 'PENDING ⏳', detail: 'Order is pending execution or waiting for conditions.' },
            { key: 'FILLED', label: 'FILLED 🎉', detail: 'Order filled and now active.' },
            { key: 'REJECT', label: 'REJECT ❌', detail: 'Signal or order rejected by risk rules.' },
            { key: 'KILLED', label: 'KILLED ⚡', detail: 'Symbol or global kill switch was triggered.' },
        ];

        const normalized = message.toUpperCase();
        tags.forEach((entry) => {
            if (normalized.includes(entry.key)) {
                pills.push(`<span class="logic-pill" data-details="${entry.detail}" title="${entry.detail}">${entry.label}</span>`);
            }
        });

        if (normalized.match(/\b0\.[0-9]+\b/)) {
            const detail = `Conviction level in message (${message}).`;
            pills.push(`<span class="logic-pill" data-details="${detail}" title="${detail}">Conviction</span>`);
        }

        // Ensure at least one pill shown for reading
        if (pills.length === 0) {
            pills.push(`<span class="logic-pill" data-details="General logic update" title="General logic update">Logic</span>`);
        }

        return pills;
    }

    updateExecutionTimeline(signals) {
        const timeline = document.getElementById('executionTimeline');
        if (!timeline) return;
        timeline.innerHTML = '';

        if (!signals || signals.length === 0) {
            timeline.innerHTML = '<div class="timeline-item">No upcoming entries</div>';
            return;
        }

        const sorted = (signals || []).slice().sort((a, b) => (b.conviction || 0) - (a.conviction || 0)).slice(0, 6);
        sorted.forEach((s) => {
            const item = document.createElement('div');
            const entryPrice = s.entry || 0;
            const currentPrice = s.current_price || entryPrice || 0;
            const pips = entryPrice && currentPrice ? Math.abs((entryPrice - currentPrice) / (entryPrice > 10 ? 0.0001 : 0.01)).toFixed(1) : 'n/a';
            const color = (s.conviction || 0) >= 0.6 ? '#10b981' : (s.conviction || 0) >= 0.35 ? '#f59e0b' : '#ef4444';

            item.className = 'timeline-item';
            item.style.borderColor = color;
            item.innerHTML = `
                <strong>${s.symbol || 'N/A'}</strong><br>
                ${s.type || s.nature || 'N/A'}<br>
                <span>${pips} pips to entry</span>
            `;
            timeline.appendChild(item);
        });
    }

    showLogDetails(log) {
        const modal = document.getElementById('logModal');
        const details = document.getElementById('logDetails');
        if (!modal || !details) {
            console.warn('Log details modal not found');
            return;
        }
        const entries = this.summarizeLogDetails(log).filter(([k]) => k !== 'timestamp');
        details.innerHTML = `
            <div class="log-detail-actions">
                ${this.buildExecuteButtonHtml(log, 'modal-log-execute-btn')}
            </div>
            <p><strong>Timestamp:</strong> <span>${new Date(log.timestamp).toLocaleString()}</span></p>
            ${entries
                .map(
                    ([key, value]) =>
                        `<p><strong>${this.escapeHtml(this.labelize(key))}:</strong> <span>${this.formatLogValue(value)}</span></p>`
                )
                .join('')}
        `;
        details.querySelector('.modal-log-execute-btn')?.addEventListener('click', (event) => {
            event.stopPropagation();
            this.executeSignal(log, null, 'payload');
        });
        modal.style.display = 'block';
    }

    showSignalDetails(signal) {
        const modal = document.getElementById('signalModal');
        const details = document.getElementById('signalDetails');
        if (!modal || !details) {
            console.warn('Signal details modal not found');
            return;
        }

        const fields = [
            ['Symbol', signal.symbol],
            ['Signal', signal.nature || signal.type],
            ['Entry', signal.entry ? signal.entry.toFixed(5) : 'N/A'],
            ['SL', signal.sl ? signal.sl.toFixed(5) : 'N/A'],
            ['TP', signal.tp ? signal.tp.toFixed(5) : 'N/A'],
            ['Style', signal.trade_style || 'N/A'],
            ['Execution Method', this.getExecutionMethodText(signal)],
            ['Trade Type', signal.trade_horizon ? `${signal.trade_horizon.type} (${signal.trade_horizon.hold_time})` : 'N/A'],
            ['Trade Type Reason', signal.trade_horizon ? signal.trade_horizon.reason : 'N/A'],
            ['MTF Analytic', this.getMtfSummary(signal).detail],
            ['Conviction', signal.conviction != null ? signal.conviction.toFixed(3) : 'N/A'],
            ['Confluence', signal.confluence_score != null ? signal.confluence_score.toFixed(3) : 'N/A'],
            ['Early Setup Score', signal.setup_score ? `${signal.setup_score.score.toFixed(3)} (${signal.setup_score.grade})` : 'N/A'],
            ['Setup Archetype', signal.setup_score ? signal.setup_score.archetype : 'N/A'],
            ['Setup Summary', signal.setup_score ? signal.setup_score.summary : 'N/A'],
            ['Liquidity Sweep', signal.liquidity_sweep ? signal.liquidity_sweep.description : 'N/A'],
            ['MSS/BOS', signal.market_structure_shift ? signal.market_structure_shift.description : 'N/A'],
            ['HTF Bias', signal.higher_timeframe_bias ? signal.higher_timeframe_bias.description : 'N/A'],
            ['Session', signal.session_bias ? `${signal.session_bias.session}: ${signal.session_bias.description}` : 'N/A'],
            ['Displacement', signal.displacement ? signal.displacement.description : 'N/A'],
            ['Premium/Discount', signal.premium_discount ? signal.premium_discount.description : 'N/A'],
            ['Spread', signal.spread_safety ? signal.spread_safety.description : 'N/A'],
            ['Scalp Potential', signal.scalp_potential ? signal.scalp_potential.label : 'N/A'],
            ['Trend Strength', signal.trend_strength ? signal.trend_strength.label : 'N/A'],
            ['Order Block', signal.order_block ? signal.order_block.description : 'N/A'],
            ['Liquidity Zone', signal.liquidity_zone ? signal.liquidity_zone.description : 'N/A'],
            ['Divergence', signal.divergence ? signal.divergence.label : 'N/A'],
            ['Status', signal.status || 'N/A'],
        ];

        details.innerHTML = `
            <div class="signal-detail-actions">
                ${this.buildExecuteButtonHtml(signal, 'modal-signal-execute-btn')}
            </div>
            <div class="signal-detail-grid">
                ${fields
                    .map(
                        ([label, value]) =>
                            `<div class="signal-detail-row"><strong>${label}:</strong> <span>${value}</span></div>`
                    )
                    .join('')}
            </div>
        `;
        details.querySelector('.modal-signal-execute-btn')?.addEventListener('click', (event) => {
            event.stopPropagation();
            this.executeSignal(signal, null, 'payload');
        });
        modal.style.display = 'block';
    }

    async loadSessions() {
        try {
            const res = await fetch(`${this.apiBase}/sessions`);
            const data = await res.json();
            const list = document.getElementById('sessionsList');
            list.innerHTML = '';
            if (data.data) {
                Object.entries(data.data).forEach(([name, period]) => {
                    const li = document.createElement('li');
                    li.textContent = `${name}: ${period.start} - ${period.end}`;
                    list.appendChild(li);
                });
            }
        } catch (e) {
            console.error('Sessions error:', e);
        }
    }

    async loadKillStatus() {
        try {
            const res = await fetch(`${this.apiBase}/kill`);
            const data = await res.json();
            if (data.data) {
                const killAll = document.getElementById('killAll');
                if (killAll) killAll.textContent = data.data.all ? 'On' : 'Off';
                const sidebarKillState = document.getElementById('sidebarKillState');
                if (sidebarKillState) {
                    sidebarKillState.textContent = data.data.all ? 'Locked' : 'Ready';
                    sidebarKillState.dataset.state = data.data.all ? 'locked' : 'ready';
                }
                const sel = document.getElementById('killSymbol');
                if (!sel) return;
                sel.innerHTML = '';
                sel.appendChild(new Option('all','all'));
                this.symbols.forEach(sym => sel.appendChild(new Option(sym,sym)));
                const chk = document.getElementById('killToggle');
                if (chk) chk.checked = !!data.data[sel.value];
            }
        } catch (e) {
            console.error('Kill status error:', e);
        }
    }

    async toggleKill() {
        try {
            const symbol = document.getElementById('killSymbol').value;
            const chk = document.getElementById('killToggle');
            const action = chk.checked ? 'disable' : 'enable';
            await fetch(`${this.apiBase}/kill`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({symbol, action})
            });
            this.loadKillStatus();
        } catch(e) {
            console.error('Kill toggle error:', e);
        }
    }

    async loadSettings() {
        try {
            const res = await fetch(`${this.apiBase}/config`);
            const data = await res.json();
            if (data.data) {
                const cfg = data.data;
                // Trading Parameters
                this.setAllInputs('symbols', cfg.TRADING_SYMBOLS || 'EURUSD,GBPUSD,USDJPY,XAUUSD');
                this.setAllInputs('volume', cfg.TRADE_VOLUME || '0.001');
                this.setAllInputs('smallAccountModeEnabled', cfg.FEATURE_SMALL_ACCOUNT_MODE === true);
                this.setAllInputs('smallAccountThreshold', cfg.SMALL_ACCOUNT_EQUITY_THRESHOLD || '25');
                this.setAllInputs('smallAccountTradeVolume', cfg.SMALL_ACCOUNT_TRADE_VOLUME || '0.001');
                this.setAllInputs('smallAccountMaxAutoMinLot', cfg.SMALL_ACCOUNT_MAX_AUTO_MIN_LOT || '0.01');
                this.setAllInputs('smallAccountMaxExposurePct', cfg.SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT || '1');
                this.setAllInputs('smallAccountMaxActiveTrades', cfg.SMALL_ACCOUNT_MAX_ACTIVE_TRADES || '1');
                this.setAllInputs('smallAccountAllowMetals', cfg.SMALL_ACCOUNT_ALLOW_METALS === true);
                this.setAllInputs('smallAccountAllowCrypto', cfg.SMALL_ACCOUNT_ALLOW_CRYPTO === true);
                this.setAllInputs('smallAccountAllowStocks', cfg.SMALL_ACCOUNT_ALLOW_STOCKS === true);
                this.setAllInputs('smallAccountDisableNewsLadder', cfg.SMALL_ACCOUNT_DISABLE_NEWS_LADDER !== false);
                this.setAllInputs('smallAccountDisablePendingOrders', cfg.SMALL_ACCOUNT_DISABLE_PENDING_ORDERS !== false);

                // Risk Management
                this.setAllInputs('maxExposurePct', cfg.MAX_EXPOSURE_PERCENT || '5');
                this.setAllInputs('minProfitPips', cfg.MIN_PROFIT_PIPS || '50');
                this.setAllInputs('dailyProfitCap', cfg.DAILY_PROFIT_CAP || '2.0');
                this.setAllInputs('maxDailyLosses', cfg.MAX_DAILY_LOSSES ?? '100');
                this.setAllInputs('maxConsecutiveLosses', cfg.MAX_CONSECUTIVE_LOSSES ?? '30');
                this.setAllInputs('minExpectedR', cfg.MIN_EXPECTED_R || '1.2');
                this.setAllInputs('takeProfitR', cfg.TAKE_PROFIT_R_MULTIPLIER || '1.5');
                this.setAllInputs('takeProfitRScalp', cfg.TAKE_PROFIT_R_MULTIPLIER_SCALP || '1.2');
                this.setAllInputs('trailingStopTriggerPct', cfg.TRAILING_STOP_TRIGGER_PCT || '55');
                this.setAllInputs('trailingStopTriggerR', cfg.TRAILING_STOP_TRIGGER_R != null ? cfg.TRAILING_STOP_TRIGGER_R : '-1');
                this.setAllInputs('trailingStopLockPips', cfg.TRAILING_STOP_LOCK_PIPS || '10');
                this.setAllInputs('trailingStopStepPct', cfg.TRAILING_STOP_STEP_PCT || '50');
                this.setAllInputs('trailingStopMinStepPips', cfg.TRAILING_STOP_MIN_STEP_PIPS || '5');
                this.setAllInputs('trailingTpEnabled', cfg.FEATURE_TRAILING_TAKE_PROFIT !== false);
                this.setAllInputs('trailingTpTriggerPct', cfg.TRAILING_TP_TRIGGER_PCT || '80');
                this.setAllInputs('trailingTpExtensionPct', cfg.TRAILING_TP_EXTENSION_PCT || '50');
                this.setAllInputs('trailingTpCooldownSeconds', cfg.TRAILING_TP_COOLDOWN_SECONDS || '300');
                this.setAllInputs('partialTpExtendEnabled', cfg.FEATURE_PARTIAL_TP_EXTEND !== false);
                this.setAllInputs('partialTpExtendPct', cfg.PARTIAL_TP_EXTEND_PCT || '50');
                this.setAllInputs('partialTpEnabled', cfg.FEATURE_PARTIAL_TAKE_PROFIT !== false);
                this.setAllInputs('partialTpTriggerR', cfg.PARTIAL_TP_TRIGGER_R || '0.75');
                this.setAllInputs('partialTpClosePct', cfg.PARTIAL_TP_CLOSE_PCT || '50');
                this.setAllInputs('partialTpLockPips', cfg.PARTIAL_TP_LOCK_PIPS || '10');
                this.setAllInputs('breakevenProtectionEnabled', cfg.FEATURE_BREAKEVEN_PROTECTION !== false);
                this.setAllInputs('firstProfitBreakevenEnabled', cfg.FEATURE_FIRST_PROFIT_BREAKEVEN !== false);
                this.setAllInputs('firstProfitBreakevenTriggerR', cfg.FIRST_PROFIT_BREAKEVEN_TRIGGER_R || '0.10');
                this.setAllInputs('firstProfitBreakevenTriggerRScalp', cfg.FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP || '0.08');
                this.setAllInputs('breakevenTriggerR', cfg.BREAKEVEN_TRIGGER_R || '0.30');
                this.setAllInputs('breakevenLockPips', cfg.BREAKEVEN_LOCK_PIPS || '0');
                this.setAllInputs('reversalBreakevenAtEntryEnabled', cfg.FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY !== false);
                this.setAllInputs('maxAdverseExitEnabled', cfg.FEATURE_MAX_ADVERSE_EXIT !== false);
                this.setAllInputs('maxAdverseR', cfg.MAX_ADVERSE_R || '0.60');
                this.setAllInputs('reverseProfitExitEnabled', cfg.FEATURE_REVERSE_PROFIT_EXIT !== false);
                this.setAllInputs('reverseProfitMinR', cfg.REVERSE_PROFIT_MIN_R || '1.20');
                this.setAllInputs('reverseProfitGivebackPct', cfg.REVERSE_PROFIT_GIVEBACK_PCT || '45');
                this.setAllInputs('reverseProfitClosePct', cfg.REVERSE_PROFIT_CLOSE_PCT || '50');
                this.setAllInputs('reverseAfterPartialLockR', cfg.REVERSE_AFTER_PARTIAL_LOCK_R || '0.20');

                // Signal Lockout System
                this.setAllInputs('signalLockoutEnabled', cfg.SIGNAL_LOCKOUT_ENABLED !== false);
                this.setAllInputs('maxTradesPerSymbol', cfg.MAX_TRADES_PER_SYMBOL || '1');
                this.setAllInputs('maxActiveTradesTotal', cfg.MAX_ACTIVE_TRADES_TOTAL || '10');
                this.setAllInputs('tradeCooldownMinutes', cfg.TRADE_COOLDOWN_MINUTES || '15');
                this.setAllInputs('noRevengeCooldown', cfg.NO_REVENGE_COOLDOWN_SECONDS ? cfg.NO_REVENGE_COOLDOWN_SECONDS / 3600 : '24');
                this.setAllInputs('reversalShockGuardEnabled', cfg.FEATURE_REVERSAL_SHOCK_GUARD !== false);
                this.setAllInputs('reversalShockCooldownMinutes', cfg.REVERSAL_SHOCK_COOLDOWN_MINUTES || '30');
                this.setAllInputs('reversalShockXauCooldownMinutes', cfg.REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES || '60');
                this.setAllInputs('opposingSignalProfitExitEnabled', cfg.FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT !== false);
                this.setAllInputs('opposingSignalMinR', cfg.OPPOSING_SIGNAL_MIN_R || '0.20');
                this.setAllInputs('opposingSignalMinScore', cfg.OPPOSING_SIGNAL_MIN_SCORE || '0.58');
                this.setAllInputs('professionalGateEnabled', cfg.FEATURE_PROFESSIONAL_EXECUTION_GATE !== false);
                this.setAllInputs('minExecutionGrade', cfg.MIN_EXECUTION_GRADE || 'B');
                this.setAllInputs('allowCGradeScalps', cfg.ALLOW_C_GRADE_SCALPS === true);
                this.setAllInputs('minProfessionalScore', cfg.MIN_PROFESSIONAL_SETUP_SCORE || '0.62');
                this.setAllInputs('minProfessionalConviction', cfg.MIN_PROFESSIONAL_CONVICTION || '0.30');
                this.setAllInputs('minSessionScoreForTrade', cfg.MIN_SESSION_SCORE_FOR_TRADE || '0.45');
                this.setAllInputs('earlyEntryEnabled', cfg.FEATURE_EARLY_ENTRY !== false);
                this.setAllInputs('earlyEntryMinScore', cfg.EARLY_ENTRY_MIN_SCORE || '0.50');
                this.setAllInputs('featureIctMode', cfg.FEATURE_ICT_MODE !== false);
                this.safeSetText('ictMode', cfg.FEATURE_ICT_MODE !== false ? 'Enabled' : 'Disabled');
                this.setAllInputs('ictMinSetupScore', cfg.ICT_MIN_SETUP_SCORE || '0.60');
                this.setAllInputs('ictMinConfluence', cfg.ICT_MIN_CONFLUENCE || '0.60');
                this.setAllInputs('minSessionScoreForScalp', cfg.MIN_SESSION_SCORE_FOR_SCALP || '0.65');
                this.setAllInputs('instrumentProfilesEnabled', cfg.FEATURE_INSTRUMENT_PROFILES !== false);
                this.setAllInputs('analyticTimeframes', cfg.ANALYTIC_TIMEFRAMES || 'M1,M5,M15,H1,H4');
                this.setAllInputs('mtfExecutionGateEnabled', cfg.FEATURE_MTF_EXECUTION_GATE !== false);
                this.setAllInputs('minMtfExecutionScore', cfg.MIN_MTF_EXECUTION_SCORE || '0.30');
                this.setAllInputs('minMtfExecutionScoreMetal', cfg.MIN_MTF_EXECUTION_SCORE_METAL || '0.45');
                this.setAllInputs('scalpExecutionSetupScoreThreshold', cfg.SCALP_EXECUTION_SETUP_SCORE_THRESHOLD || '0.52');
                this.setAllInputs('scalpExecutionConvictionThreshold', cfg.SCALP_EXECUTION_CONVICTION_THRESHOLD || '0.28');
                this.setAllInputs('scalpRequireHardStructure', cfg.SCALP_REQUIRE_HARD_STRUCTURE !== false);
                this.setAllInputs('intradayExecutionSetupScoreThreshold', cfg.INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD || '0.50');
                this.setAllInputs('intradayExecutionConvictionThreshold', cfg.INTRADAY_EXECUTION_CONVICTION_THRESHOLD || '0.35');
                this.setAllInputs('swingExecutionSetupScoreThreshold', cfg.SWING_EXECUTION_SETUP_SCORE_THRESHOLD || '0.68');
                this.setAllInputs('swingExecutionConvictionThreshold', cfg.SWING_EXECUTION_CONVICTION_THRESHOLD || '0.42');
                this.setAllInputs('swingRequireHtf', cfg.SWING_REQUIRE_HTF !== false);
                this.setAllInputs('executionSetupScoreThreshold', cfg.EXECUTION_SETUP_SCORE_THRESHOLD || '0.45');
                this.setAllInputs('executionArchetypeScoreThreshold', cfg.EXECUTION_ARCHETYPE_SCORE_THRESHOLD || '0.58');
                this.setAllInputs('marketExecutionScoreThreshold', cfg.MARKET_EXECUTION_SCORE_THRESHOLD || '0.60');
                this.setAllInputs('marketExecutionConvictionThreshold', cfg.MARKET_EXECUTION_CONVICTION_THRESHOLD || '0.55');
                this.setAllInputs('blockContextWatchTrades', cfg.BLOCK_CONTEXT_WATCH_TRADES !== false);
                this.setAllInputs('blockAsiaTransitionSessions', cfg.BLOCK_ASIA_TRANSITION_SESSIONS !== false);

                // MT5 Connection
                this.setAllInputs('mt5Account', cfg.MT5_ACCOUNT || '');
                this.setAllInputs('mt5Server', cfg.MT5_SERVER || '');

                this.setAllInputs('warRoomEnabled', cfg.WAR_ROOM_ENABLED !== false);
                if (cfg.ENV_ALL) {
                    const envText = Object.entries(cfg.ENV_ALL)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([key, value]) => `${key}=${value ?? ''}`)
                        .join('\n');
                    this.setAllInputs('envEditor', envText);
                }

            }
        } catch(e) {
            console.error('Settings load error:', e);
        }
    }

    setSettingsTab(tab = 'basic') {
        document.querySelectorAll('.settings-tab').forEach((btn) => {
            btn.classList.toggle('active', (btn.dataset.settingsTab || 'basic') === tab);
        });
        document.querySelectorAll('.settings-panel').forEach((panel) => {
            panel.classList.toggle('active', (panel.dataset.settingsPanel || 'basic') === tab);
        });
    }

    buildSettingsGroupPayload(group, form) {
        const groups = {
            trading: {
                TRADING_SYMBOLS: this.readFormValue(form, 'symbols', document.getElementById('symbols')?.value || ''),
                EXECUTION_SYMBOLS: this.readFormValue(form, 'symbols', document.getElementById('symbols')?.value || ''),
                TRADE_VOLUME: this.readNumber(form, 'volume', 0.001),
                MAX_ACTIVE_TRADES_TOTAL: Math.round(this.readNumber(form, 'maxActiveTradesTotal', 10)),
                MAX_TRADES_PER_SYMBOL: Math.round(this.readNumber(form, 'maxTradesPerSymbol', 1)),
                TRADE_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'tradeCooldownMinutes', 3)),
                SIGNAL_LOCKOUT_ENABLED: Boolean(this.readFormValue(form, 'signalLockoutEnabled', document.getElementById('signalLockoutEnabled')?.checked ?? true)),
                FEATURE_DYNAMIC_ACCOUNT_PROFILE: Boolean(this.readFormValue(form, 'dynamicAccountProfileEnabled', document.getElementById('dynamicAccountProfileEnabled')?.checked ?? true)),
            },
            execution: {
                FEATURE_PROFESSIONAL_EXECUTION_GATE: Boolean(this.readFormValue(form, 'professionalGateEnabled', document.getElementById('professionalGateEnabled')?.checked ?? true)),
                MIN_EXECUTION_GRADE: this.readFormValue(form, 'minExecutionGrade', document.getElementById('minExecutionGrade')?.value || 'B'),
                ALLOW_C_GRADE_SCALPS: Boolean(this.readFormValue(form, 'allowCGradeScalps', document.getElementById('allowCGradeScalps')?.checked ?? false)),
                MIN_PROFESSIONAL_SETUP_SCORE: this.readNumber(form, 'minProfessionalScore', 0.62),
                MIN_PROFESSIONAL_CONVICTION: this.readNumber(form, 'minProfessionalConviction', 0.30),
                MIN_SESSION_SCORE_FOR_TRADE: this.readNumber(form, 'minSessionScoreForTrade', 0.45),
                MIN_SESSION_SCORE_FOR_SCALP: this.readNumber(form, 'minSessionScoreForScalp', 0.65),
                EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'executionSetupScoreThreshold', 0.45),
                EXECUTION_ARCHETYPE_SCORE_THRESHOLD: this.readNumber(form, 'executionArchetypeScoreThreshold', 0.58),
                MARKET_EXECUTION_SCORE_THRESHOLD: this.readNumber(form, 'marketExecutionScoreThreshold', 0.60),
                MARKET_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'marketExecutionConvictionThreshold', 0.55),
                FEATURE_EARLY_ENTRY: Boolean(this.readFormValue(form, 'earlyEntryEnabled', document.getElementById('earlyEntryEnabled')?.checked ?? true)),
                EARLY_ENTRY_MIN_SCORE: this.readNumber(form, 'earlyEntryMinScore', 0.50),
                FEATURE_ICT_MODE: Boolean(this.readFormValue(form, 'featureIctMode', document.getElementById('featureIctMode')?.checked ?? false)),
                ICT_MIN_SETUP_SCORE: this.readNumber(form, 'ictMinSetupScore', 0.60),
                ICT_MIN_CONFLUENCE: this.readNumber(form, 'ictMinConfluence', 0.60),
                FEATURE_INSTRUMENT_PROFILES: Boolean(this.readFormValue(form, 'instrumentProfilesEnabled', document.getElementById('instrumentProfilesEnabled')?.checked ?? true)),
                ANALYTIC_TIMEFRAMES: this.readFormValue(form, 'analyticTimeframes', document.getElementById('analyticTimeframes')?.value || 'M1,M5,M15,H1,H4'),
                FEATURE_MTF_EXECUTION_GATE: Boolean(this.readFormValue(form, 'mtfExecutionGateEnabled', document.getElementById('mtfExecutionGateEnabled')?.checked ?? true)),
                MIN_MTF_EXECUTION_SCORE: this.readNumber(form, 'minMtfExecutionScore', 0.30),
                MIN_MTF_EXECUTION_SCORE_METAL: this.readNumber(form, 'minMtfExecutionScoreMetal', 0.45),
                SCALP_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'scalpExecutionSetupScoreThreshold', 0.52),
                SCALP_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'scalpExecutionConvictionThreshold', 0.28),
                SCALP_REQUIRE_HARD_STRUCTURE: Boolean(this.readFormValue(form, 'scalpRequireHardStructure', document.getElementById('scalpRequireHardStructure')?.checked ?? true)),
                INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'intradayExecutionSetupScoreThreshold', 0.50),
                INTRADAY_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'intradayExecutionConvictionThreshold', 0.35),
                SWING_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'swingExecutionSetupScoreThreshold', 0.68),
                SWING_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'swingExecutionConvictionThreshold', 0.42),
                SWING_REQUIRE_HTF: Boolean(this.readFormValue(form, 'swingRequireHtf', document.getElementById('swingRequireHtf')?.checked ?? true)),
                BLOCK_CONTEXT_WATCH_TRADES: Boolean(this.readFormValue(form, 'blockContextWatchTrades', document.getElementById('blockContextWatchTrades')?.checked ?? true)),
                BLOCK_ASIA_TRANSITION_SESSIONS: Boolean(this.readFormValue(form, 'blockAsiaTransitionSessions', document.getElementById('blockAsiaTransitionSessions')?.checked ?? false)),
            },
            management: {
                FEATURE_PARTIAL_TAKE_PROFIT: Boolean(this.readFormValue(form, 'partialTpEnabled', document.getElementById('partialTpEnabled')?.checked ?? true)),
                PARTIAL_TP_TRIGGER_R: this.readNumber(form, 'partialTpTriggerR', 0.75),
                PARTIAL_TP_CLOSE_PCT: this.readPercentDecimal(form, 'partialTpClosePct', 0.50),
                PARTIAL_TP_LOCK_PIPS: this.readNumber(form, 'partialTpLockPips', 10),
                FEATURE_BREAKEVEN_PROTECTION: Boolean(this.readFormValue(form, 'breakevenProtectionEnabled', document.getElementById('breakevenProtectionEnabled')?.checked ?? true)),
                FEATURE_FIRST_PROFIT_BREAKEVEN: Boolean(this.readFormValue(form, 'firstProfitBreakevenEnabled', document.getElementById('firstProfitBreakevenEnabled')?.checked ?? true)),
                FIRST_PROFIT_BREAKEVEN_TRIGGER_R: this.readNumber(form, 'firstProfitBreakevenTriggerR', 0.10),
                FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP: this.readNumber(form, 'firstProfitBreakevenTriggerRScalp', 0.08),
                BREAKEVEN_TRIGGER_R: this.readNumber(form, 'breakevenTriggerR', 0.30),
                BREAKEVEN_LOCK_PIPS: this.readNumber(form, 'breakevenLockPips', 0),
                FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY: Boolean(this.readFormValue(form, 'reversalBreakevenAtEntryEnabled', document.getElementById('reversalBreakevenAtEntryEnabled')?.checked ?? true)),
                FEATURE_PARTIAL_TP_EXTEND: Boolean(this.readFormValue(form, 'partialTpExtendEnabled', document.getElementById('partialTpExtendEnabled')?.checked ?? true)),
                PARTIAL_TP_EXTEND_PCT: this.readPercentDecimal(form, 'partialTpExtendPct', 0.50),
                FEATURE_TRAILING_TAKE_PROFIT: Boolean(this.readFormValue(form, 'trailingTpEnabled', document.getElementById('trailingTpEnabled')?.checked ?? true)),
                TRAILING_TP_TRIGGER_PCT: this.readPercentDecimal(form, 'trailingTpTriggerPct', 0.80),
                TRAILING_TP_EXTENSION_PCT: this.readPercentDecimal(form, 'trailingTpExtensionPct', 0.50),
                TRAILING_TP_COOLDOWN_SECONDS: Math.round(this.readNumber(form, 'trailingTpCooldownSeconds', 300)),
                TRAILING_STOP_TRIGGER_PCT: this.readPercentDecimal(form, 'trailingStopTriggerPct', 0.55),
                TRAILING_STOP_LOCK_PIPS: this.readNumber(form, 'trailingStopLockPips', 10),
                TRAILING_STOP_STEP_PCT: this.readPercentDecimal(form, 'trailingStopStepPct', 0.50),
                TRAILING_STOP_MIN_STEP_PIPS: this.readNumber(form, 'trailingStopMinStepPips', 5),
                FEATURE_REVERSE_PROFIT_EXIT: Boolean(this.readFormValue(form, 'reverseProfitExitEnabled', document.getElementById('reverseProfitExitEnabled')?.checked ?? true)),
                REVERSE_PROFIT_MIN_R: this.readNumber(form, 'reverseProfitMinR', 1.20),
                REVERSE_PROFIT_GIVEBACK_PCT: this.readPercentDecimal(form, 'reverseProfitGivebackPct', 0.45),
                REVERSE_PROFIT_CLOSE_PCT: this.readPercentDecimal(form, 'reverseProfitClosePct', 0.50),
                REVERSE_AFTER_PARTIAL_LOCK_R: this.readNumber(form, 'reverseAfterPartialLockR', 0.20),
                FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT: Boolean(this.readFormValue(form, 'opposingSignalProfitExitEnabled', document.getElementById('opposingSignalProfitExitEnabled')?.checked ?? true)),
                OPPOSING_SIGNAL_MIN_R: this.readNumber(form, 'opposingSignalMinR', 0.20),
                OPPOSING_SIGNAL_MIN_SCORE: this.readNumber(form, 'opposingSignalMinScore', 0.58),
            },
            risk: {
                MAX_EXPOSURE_PERCENT: this.readPercentDecimal(form, 'maxExposurePct', 0.05),
                DAILY_PROFIT_CAP: this.readPercentDecimal(form, 'dailyProfitCap', 0.02),
                MAX_DAILY_LOSSES: Math.round(this.readNumber(form, 'maxDailyLosses', 100)),
                MAX_CONSECUTIVE_LOSSES: Math.round(this.readNumber(form, 'maxConsecutiveLosses', 30)),
                FEATURE_SMALL_ACCOUNT_MODE: Boolean(this.readFormValue(form, 'smallAccountModeEnabled', document.getElementById('smallAccountModeEnabled')?.checked ?? false)),
                SMALL_ACCOUNT_EQUITY_THRESHOLD: this.readNumber(form, 'smallAccountThreshold', 25),
                SMALL_ACCOUNT_TRADE_VOLUME: this.readNumber(form, 'smallAccountTradeVolume', 0.001),
                SMALL_ACCOUNT_MAX_AUTO_MIN_LOT: this.readNumber(form, 'smallAccountMaxAutoMinLot', 0.01),
                SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT: this.readPercentDecimal(form, 'smallAccountMaxExposurePct', 0.01),
                SMALL_ACCOUNT_MAX_ACTIVE_TRADES: Math.round(this.readNumber(form, 'smallAccountMaxActiveTrades', 1)),
                SMALL_ACCOUNT_ALLOW_METALS: Boolean(this.readFormValue(form, 'smallAccountAllowMetals', document.getElementById('smallAccountAllowMetals')?.checked ?? false)),
                SMALL_ACCOUNT_ALLOW_CRYPTO: Boolean(this.readFormValue(form, 'smallAccountAllowCrypto', document.getElementById('smallAccountAllowCrypto')?.checked ?? false)),
                SMALL_ACCOUNT_ALLOW_STOCKS: Boolean(this.readFormValue(form, 'smallAccountAllowStocks', document.getElementById('smallAccountAllowStocks')?.checked ?? false)),
                SMALL_ACCOUNT_DISABLE_NEWS_LADDER: Boolean(this.readFormValue(form, 'smallAccountDisableNewsLadder', document.getElementById('smallAccountDisableNewsLadder')?.checked ?? true)),
                SMALL_ACCOUNT_DISABLE_PENDING_ORDERS: Boolean(this.readFormValue(form, 'smallAccountDisablePendingOrders', document.getElementById('smallAccountDisablePendingOrders')?.checked ?? true)),
                FEATURE_MAX_ADVERSE_EXIT: Boolean(this.readFormValue(form, 'maxAdverseExitEnabled', document.getElementById('maxAdverseExitEnabled')?.checked ?? true)),
                MAX_ADVERSE_R: this.readNumber(form, 'maxAdverseR', 0.60),
                FEATURE_REVERSAL_SHOCK_GUARD: Boolean(this.readFormValue(form, 'reversalShockGuardEnabled', document.getElementById('reversalShockGuardEnabled')?.checked ?? true)),
                REVERSAL_SHOCK_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'reversalShockCooldownMinutes', 30)),
                REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'reversalShockXauCooldownMinutes', 60)),
                NO_REVENGE_COOLDOWN_SECONDS: Math.round(this.readNumber(form, 'noRevengeCooldown', 24)) * 3600,
            },
            env: {
                ENV_ALL: this.readFormValue(form, 'envEditor', document.getElementById('envEditor')?.value || ''),
            },
        };
        const payload = groups[group] || {};
        Object.keys(payload).forEach((key) => {
            if (typeof payload[key] === 'number' && !Number.isFinite(payload[key])) {
                delete payload[key];
            }
        });
        return payload;
    }

    async saveSettingsGroup(e) {
        const button = e.currentTarget;
        const group = button?.dataset?.configGroup || 'settings';
        const form = button?.closest('form') || document.getElementById('settingsForm') || document.getElementById('settingsModalForm');
        const originalText = button?.innerHTML;
        if (button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        }
        this.showNotification(`Saving ${group} settings...`, 'info');
        try {
            const config = this.buildSettingsGroupPayload(group, form);
            const res = await fetch(`${this.apiBase}/config`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(config)
            });
            const rawResponse = await res.text();
            let data = {};
            try {
                data = rawResponse ? JSON.parse(rawResponse) : {};
            } catch (parseError) {
                data = {status: 'error', message: rawResponse || parseError.message};
            }
            if (res.ok && data.status === 'success') {
                await this.loadSettings();
                await this.updateDashboard();
                this.showNotification(`${group.charAt(0).toUpperCase() + group.slice(1)} settings saved and applied.`, 'success');
            } else {
                this.showNotification(`Configuration error: ${data.message || data.error || rawResponse || `HTTP ${res.status}`}`, 'error');
            }
        } catch (err) {
            this.showNotification(`Configuration error: ${err.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalText;
            }
        }
    }

    async saveSettings(e) {
        e.preventDefault();
        const form = e.target;
        const submitter = e.submitter || form?.querySelector('button[type="submit"]');
        const originalSubmitText = submitter?.innerHTML;
        if (submitter) {
            submitter.disabled = true;
            submitter.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        }
        this.showNotification('Saving configuration...', 'info');
        try {
            const config = {
                // Trading Parameters
                TRADING_SYMBOLS: this.readFormValue(form, 'symbols', document.getElementById('symbols')?.value || ''),
                EXECUTION_SYMBOLS: this.readFormValue(form, 'symbols', document.getElementById('symbols')?.value || ''),
                TRADE_VOLUME: this.readNumber(form, 'volume', 0.001),

                // Risk Management
                MAX_EXPOSURE_PERCENT: this.readPercentDecimal(form, 'maxExposurePct', 0.05),
                MIN_PROFIT_PIPS: this.readNumber(form, 'minProfitPips', 10),
                DAILY_PROFIT_CAP: this.readPercentDecimal(form, 'dailyProfitCap', 0.02),
                MAX_DAILY_LOSSES: Math.round(this.readNumber(form, 'maxDailyLosses', 100)),
                MAX_CONSECUTIVE_LOSSES: Math.round(this.readNumber(form, 'maxConsecutiveLosses', 30)),
                FEATURE_SMALL_ACCOUNT_MODE: Boolean(this.readFormValue(form, 'smallAccountModeEnabled', document.getElementById('smallAccountModeEnabled')?.checked ?? false)),
                SMALL_ACCOUNT_EQUITY_THRESHOLD: this.readNumber(form, 'smallAccountThreshold', 25),
                SMALL_ACCOUNT_TRADE_VOLUME: this.readNumber(form, 'smallAccountTradeVolume', 0.001),
                SMALL_ACCOUNT_MAX_AUTO_MIN_LOT: this.readNumber(form, 'smallAccountMaxAutoMinLot', 0.01),
                SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT: this.readPercentDecimal(form, 'smallAccountMaxExposurePct', 0.01),
                SMALL_ACCOUNT_MAX_ACTIVE_TRADES: Math.round(this.readNumber(form, 'smallAccountMaxActiveTrades', 1)),
                SMALL_ACCOUNT_ALLOW_METALS: Boolean(this.readFormValue(form, 'smallAccountAllowMetals', document.getElementById('smallAccountAllowMetals')?.checked ?? false)),
                SMALL_ACCOUNT_ALLOW_CRYPTO: Boolean(this.readFormValue(form, 'smallAccountAllowCrypto', document.getElementById('smallAccountAllowCrypto')?.checked ?? false)),
                SMALL_ACCOUNT_ALLOW_STOCKS: Boolean(this.readFormValue(form, 'smallAccountAllowStocks', document.getElementById('smallAccountAllowStocks')?.checked ?? false)),
                SMALL_ACCOUNT_DISABLE_NEWS_LADDER: Boolean(this.readFormValue(form, 'smallAccountDisableNewsLadder', document.getElementById('smallAccountDisableNewsLadder')?.checked ?? true)),
                SMALL_ACCOUNT_DISABLE_PENDING_ORDERS: Boolean(this.readFormValue(form, 'smallAccountDisablePendingOrders', document.getElementById('smallAccountDisablePendingOrders')?.checked ?? true)),
                MIN_EXPECTED_R: this.readNumber(form, 'minExpectedR', 1.2),
                TAKE_PROFIT_R_MULTIPLIER: this.readNumber(form, 'takeProfitR', 1.5),
                TAKE_PROFIT_R_MULTIPLIER_SCALP: this.readNumber(form, 'takeProfitRScalp', 1.2),
                TRAILING_STOP_TRIGGER_PCT: this.readPercentDecimal(form, 'trailingStopTriggerPct', 0.55),
                TRAILING_STOP_LOCK_PIPS: this.readNumber(form, 'trailingStopLockPips', 10),
                TRAILING_STOP_STEP_PCT: this.readPercentDecimal(form, 'trailingStopStepPct', 0.50),
                TRAILING_STOP_MIN_STEP_PIPS: this.readNumber(form, 'trailingStopMinStepPips', 5),
                FEATURE_TRAILING_TAKE_PROFIT: Boolean(this.readFormValue(form, 'trailingTpEnabled', document.getElementById('trailingTpEnabled')?.checked ?? true)),
                TRAILING_TP_TRIGGER_PCT: this.readPercentDecimal(form, 'trailingTpTriggerPct', 0.80),
                TRAILING_TP_EXTENSION_PCT: this.readPercentDecimal(form, 'trailingTpExtensionPct', 0.50),
                TRAILING_TP_COOLDOWN_SECONDS: Math.round(this.readNumber(form, 'trailingTpCooldownSeconds', 300)),
                FEATURE_PARTIAL_TP_EXTEND: Boolean(this.readFormValue(form, 'partialTpExtendEnabled', document.getElementById('partialTpExtendEnabled')?.checked ?? true)),
                PARTIAL_TP_EXTEND_PCT: this.readPercentDecimal(form, 'partialTpExtendPct', 0.50),
                FEATURE_PARTIAL_TAKE_PROFIT: Boolean(this.readFormValue(form, 'partialTpEnabled', document.getElementById('partialTpEnabled')?.checked ?? true)),
                PARTIAL_TP_TRIGGER_R: this.readNumber(form, 'partialTpTriggerR', 0.75),
                PARTIAL_TP_CLOSE_PCT: this.readPercentDecimal(form, 'partialTpClosePct', 0.50),
                PARTIAL_TP_LOCK_PIPS: this.readNumber(form, 'partialTpLockPips', 10),
                TRAILING_STOP_TRIGGER_R: this.readNumber(form, 'trailingStopTriggerR', -1.0),
                FEATURE_FIRST_PROFIT_BREAKEVEN: Boolean(this.readFormValue(form, 'firstProfitBreakevenEnabled', document.getElementById('firstProfitBreakevenEnabled')?.checked ?? true)),
                FIRST_PROFIT_BREAKEVEN_TRIGGER_R: this.readNumber(form, 'firstProfitBreakevenTriggerR', 0.10),
                FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP: this.readNumber(form, 'firstProfitBreakevenTriggerRScalp', 0.08),
                FEATURE_MAX_ADVERSE_EXIT: Boolean(this.readFormValue(form, 'maxAdverseExitEnabled', document.getElementById('maxAdverseExitEnabled')?.checked ?? true)),
                MAX_ADVERSE_R: this.readNumber(form, 'maxAdverseR', 0.60),
                FEATURE_REVERSE_PROFIT_EXIT: Boolean(this.readFormValue(form, 'reverseProfitExitEnabled', document.getElementById('reverseProfitExitEnabled')?.checked ?? true)),
                REVERSE_PROFIT_MIN_R: this.readNumber(form, 'reverseProfitMinR', 1.20),
                REVERSE_PROFIT_GIVEBACK_PCT: this.readPercentDecimal(form, 'reverseProfitGivebackPct', 0.45),
                REVERSE_PROFIT_CLOSE_PCT: this.readPercentDecimal(form, 'reverseProfitClosePct', 0.50),
                REVERSE_AFTER_PARTIAL_LOCK_R: this.readNumber(form, 'reverseAfterPartialLockR', 0.20),

                // Signal Lockout System
                SIGNAL_LOCKOUT_ENABLED: Boolean(this.readFormValue(form, 'signalLockoutEnabled', document.getElementById('signalLockoutEnabled')?.checked ?? true)),
                MAX_ACTIVE_TRADES_TOTAL: Math.round(this.readNumber(form, 'maxActiveTradesTotal', 10)),
                MAX_TRADES_PER_SYMBOL: Math.round(this.readNumber(form, 'maxTradesPerSymbol', 1)),
                TRADE_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'tradeCooldownMinutes', 3)),
                NO_REVENGE_COOLDOWN_SECONDS: Math.round(this.readNumber(form, 'noRevengeCooldown', 24)) * 3600,
                FEATURE_REVERSAL_SHOCK_GUARD: Boolean(this.readFormValue(form, 'reversalShockGuardEnabled', document.getElementById('reversalShockGuardEnabled')?.checked ?? true)),
                REVERSAL_SHOCK_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'reversalShockCooldownMinutes', 30)),
                REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES: Math.round(this.readNumber(form, 'reversalShockXauCooldownMinutes', 60)),
                FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT: Boolean(this.readFormValue(form, 'opposingSignalProfitExitEnabled', document.getElementById('opposingSignalProfitExitEnabled')?.checked ?? true)),
                OPPOSING_SIGNAL_MIN_R: this.readNumber(form, 'opposingSignalMinR', 0.20),
                OPPOSING_SIGNAL_MIN_SCORE: this.readNumber(form, 'opposingSignalMinScore', 0.58),
                FEATURE_PROFESSIONAL_EXECUTION_GATE: Boolean(this.readFormValue(form, 'professionalGateEnabled', document.getElementById('professionalGateEnabled')?.checked ?? true)),
                MIN_EXECUTION_GRADE: this.readFormValue(form, 'minExecutionGrade', document.getElementById('minExecutionGrade')?.value || 'B'),
                ALLOW_C_GRADE_SCALPS: Boolean(this.readFormValue(form, 'allowCGradeScalps', document.getElementById('allowCGradeScalps')?.checked ?? false)),
                MIN_PROFESSIONAL_SETUP_SCORE: this.readNumber(form, 'minProfessionalScore', 0.62),
                MIN_PROFESSIONAL_CONVICTION: this.readNumber(form, 'minProfessionalConviction', 0.30),
                MIN_SESSION_SCORE_FOR_TRADE: this.readNumber(form, 'minSessionScoreForTrade', 0.45),
                FEATURE_EARLY_ENTRY: Boolean(this.readFormValue(form, 'earlyEntryEnabled', document.getElementById('earlyEntryEnabled')?.checked ?? true)),
                EARLY_ENTRY_MIN_SCORE: this.readNumber(form, 'earlyEntryMinScore', 0.50),
                FEATURE_ICT_MODE: Boolean(this.readFormValue(form, 'featureIctMode', document.getElementById('featureIctMode')?.checked ?? false)),
                ICT_MIN_SETUP_SCORE: this.readNumber(form, 'ictMinSetupScore', 0.60),
                ICT_MIN_CONFLUENCE: this.readNumber(form, 'ictMinConfluence', 0.60),
                MIN_SESSION_SCORE_FOR_SCALP: this.readNumber(form, 'minSessionScoreForScalp', 0.65),
                FEATURE_INSTRUMENT_PROFILES: Boolean(this.readFormValue(form, 'instrumentProfilesEnabled', document.getElementById('instrumentProfilesEnabled')?.checked ?? true)),
                ANALYTIC_TIMEFRAMES: this.readFormValue(form, 'analyticTimeframes', document.getElementById('analyticTimeframes')?.value || 'M1,M5,M15,H1,H4'),
                FEATURE_MTF_EXECUTION_GATE: Boolean(this.readFormValue(form, 'mtfExecutionGateEnabled', document.getElementById('mtfExecutionGateEnabled')?.checked ?? true)),
                MIN_MTF_EXECUTION_SCORE: this.readNumber(form, 'minMtfExecutionScore', 0.30),
                MIN_MTF_EXECUTION_SCORE_METAL: this.readNumber(form, 'minMtfExecutionScoreMetal', 0.45),
                SCALP_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'scalpExecutionSetupScoreThreshold', 0.52),
                SCALP_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'scalpExecutionConvictionThreshold', 0.28),
                SCALP_REQUIRE_HARD_STRUCTURE: Boolean(this.readFormValue(form, 'scalpRequireHardStructure', document.getElementById('scalpRequireHardStructure')?.checked ?? true)),
                INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'intradayExecutionSetupScoreThreshold', 0.50),
                INTRADAY_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'intradayExecutionConvictionThreshold', 0.35),
                SWING_EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'swingExecutionSetupScoreThreshold', 0.68),
                SWING_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'swingExecutionConvictionThreshold', 0.42),
                SWING_REQUIRE_HTF: Boolean(this.readFormValue(form, 'swingRequireHtf', document.getElementById('swingRequireHtf')?.checked ?? true)),
                EXECUTION_SETUP_SCORE_THRESHOLD: this.readNumber(form, 'executionSetupScoreThreshold', 0.45),
                EXECUTION_ARCHETYPE_SCORE_THRESHOLD: this.readNumber(form, 'executionArchetypeScoreThreshold', 0.58),
                MARKET_EXECUTION_SCORE_THRESHOLD: this.readNumber(form, 'marketExecutionScoreThreshold', 0.60),
                MARKET_EXECUTION_CONVICTION_THRESHOLD: this.readNumber(form, 'marketExecutionConvictionThreshold', 0.55),
                BLOCK_CONTEXT_WATCH_TRADES: Boolean(this.readFormValue(form, 'blockContextWatchTrades', document.getElementById('blockContextWatchTrades')?.checked ?? true)),
                BLOCK_ASIA_TRANSITION_SESSIONS: Boolean(this.readFormValue(form, 'blockAsiaTransitionSessions', document.getElementById('blockAsiaTransitionSessions')?.checked ?? false)),

                // MT5 Connection
                MT5_ACCOUNT: this.readFormValue(form, 'mt5Account', ''),
                MT5_SERVER: this.readFormValue(form, 'mt5Server', ''),

                WAR_ROOM_ENABLED: Boolean(this.readFormValue(form, 'warRoomEnabled', document.getElementById('warRoomEnabled')?.checked ?? true)),
                FEATURE_WAR_ROOM: Boolean(this.readFormValue(form, 'warRoomEnabled', document.getElementById('warRoomEnabled')?.checked ?? true)),
                ENV_ALL: this.readFormValue(form, 'envEditor', document.getElementById('envEditor')?.value || ''),

            };

            ['MT5_ACCOUNT', 'MT5_SERVER'].forEach((key) => {
                if (String(config[key] ?? '').trim() === '') {
                    delete config[key];
                }
            });
            Object.keys(config).forEach((key) => {
                if (typeof config[key] === 'number' && !Number.isFinite(config[key])) {
                    delete config[key];
                }
            });

            const res = await fetch(`${this.apiBase}/config`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(config)
            });
            const rawResponse = await res.text();
            let data = {};
            try {
                data = rawResponse ? JSON.parse(rawResponse) : {};
            } catch (parseError) {
                data = {status: 'error', message: rawResponse || parseError.message};
            }
            if (res.ok && data.status === 'success') {
                await this.loadSettings();
                await this.updateDashboard();
                if (data.verified === false) {
                    const mismatchDetail = data.mismatches ? ` ${JSON.stringify(data.mismatches)}` : '';
                    this.showNotification(`Settings saved, with verification warnings.${mismatchDetail}`, 'info');
                } else {
                    const verified = data.verified ? ' Verified in .env.' : '';
                    this.showNotification(`Settings saved and applied.${verified}`, 'success');
                }
            } else {
                const mismatchDetail = data.mismatches ? ` ${JSON.stringify(data.mismatches)}` : '';
                const skippedDetail = data.skipped_verify_keys?.length ? ` Skipped runtime-only keys: ${data.skipped_verify_keys.join(', ')}` : '';
                const detail = `${data.message || data.error || rawResponse || `HTTP ${res.status}`}${mismatchDetail}${skippedDetail}`;
                this.showNotification(`Configuration error: ${detail}`, 'error');
            }
        } catch(e) {
            this.showNotification('Configuration error: server did not complete the save request. Restart the dashboard/bot process, then try again. Detail: ' + e.message, 'error');
        } finally {
            if (submitter) {
                submitter.disabled = false;
                if (originalSubmitText) submitter.innerHTML = originalSubmitText;
            }
        }
    }

    async exportSettings() {
        try {
            const res = await fetch(`${this.apiBase}/config`);
            const data = await res.json();
            if (data.data) {
                const configJson = JSON.stringify(data.data, null, 2);
                const blob = new Blob([configJson], { type: 'application/json' });
                const url = URL.createObjectURL(blob);

                const a = document.createElement('a');
                a.href = url;
                a.download = `nexus-trading-config-${new Date().toISOString().split('T')[0]}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            } else {
                alert('No configuration data available to export.');
            }
        } catch(e) {
            alert('Export error: ' + e.message);
        }
    }

    async loadFutureTrades() {
        try {
            const response = await fetch(`${this.apiBase}/logs`);
            const data = await response.json();
            
            const grid = document.getElementById("globalRadarGrid");
            const emptyState = document.getElementById("globalRadarEmpty");
            grid.innerHTML = "";
            
            const futureTrades = (data.data && data.data.future_trades) ? data.data.future_trades : [];
            this.radarTrades = futureTrades.slice().reverse();
            this.renderRadarSummary(futureTrades);
            this.renderGlobalRadar();
            return;
            
            if (futureTrades.length > 0) {
                emptyState.style.display = "none";
                grid.style.display = "grid";
                
                futureTrades.slice().reverse().forEach((trade) => {
                    const card = document.createElement("div");
                    card.className = "watchlist-card";
                    card.style.cursor = 'pointer';
                    
                    const convictionScore = trade.conviction_score || 0;
                    const phase = trade.phase || "Monitoring";
                    const actionNeeded = trade.action_needed || "Waiting";
                    const setupName = trade.setup_name || "Setup Detected";
                    const trigger = trade.trigger || "Awaiting confirmation";
                    const setupScore = trade.setup_score?.score || trade.confluence_score || 0;
                    const setupGrade = trade.setup_score?.grade || '-';
                    const archetype = trade.setup_score?.archetype || trade.setup_name || 'Setup';
                    const components = trade.setup_score?.components || [];
                    const passedComponents = components.filter((item) => item.passed).length;
                    const spread = trade.spread_safety || trade.setup_score?.spread || {};
                    const spreadSafe = spread.safe !== false;
                    const spreadText = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)}p` : 'n/a';
                    const componentChips = components.slice(0, 4).map((item) => `
                        <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                            ${item.passed ? '✓' : '×'} ${item.label}
                        </span>
                    `).join('');
                    
                    // Status color based on conviction
                    let statusColor = "#6b7280"; // gray
                    if (convictionScore >= 80) statusColor = "#10b981"; // green
                    else if (convictionScore >= 60) statusColor = "#f59e0b"; // yellow
                    else if (convictionScore >= 40) statusColor = "#ef4444"; // red
                    
                    card.innerHTML = `
                        <div class="watchlist-card-header">
                            <div class="watchlist-card-symbol">${trade.symbol}</div>
                            <div class="watchlist-badge" style="background: ${statusColor}">${phase}</div>
                        </div>
                        <div class="watchlist-card-body">
                            <div class="watchlist-card-type">${trade.type || trade.nature || 'Signal'}</div>
                            <div class="watchlist-card-setup">${archetype}</div>
                            <div class="watchlist-card-conviction">
                                <div class="conviction-bar">
                                    <div class="conviction-fill" style="width: ${convictionScore}%"></div>
                                </div>
                                <span>${convictionScore}% Conviction</span>
                            </div>
                            <div class="watchlist-card-zone">Zone: ${trade.entry ? trade.entry.toFixed(5) : 'N/A'}</div>
                            <div class="watchlist-card-zone">Early Score: ${(setupScore * 100).toFixed(0)}% | Grade ${setupGrade}</div>
                            <div class="radar-micro-row">
                                <span>${passedComponents}/${components.length || 8} checks</span>
                                <span class="${spreadSafe ? 'metric-profit' : 'metric-loss'}">Spread ${spreadText}</span>
                            </div>
                            <div class="setup-checklist">${componentChips || '<span>No component detail</span>'}</div>
                            <div class="watchlist-card-trigger">${trigger}</div>
                            <div class="watchlist-card-action">${actionNeeded}</div>
                        </div>
                    `;

                    const signalDetails = {
                        ...trade,
                        conviction: trade.conviction_score || 0,
                        nature: trade.type || trade.nature || trade.setup_name || 'Signal',
                        trade_style: trade.setup_name || trade.phase || 'N/A',
                        scalp_potential: { label: trade.phase || 'N/A' },
                        trend_strength: { label: trade.trend_strength || 'N/A' },
                        order_block: { description: trade.order_block?.description || trade.setup_name || 'N/A' },
                        liquidity_zone: { description: trade.liquidity_zone?.description || trade.zone || 'N/A' },
                        divergence: { label: trade.divergence?.label || trade.nature || 'N/A' },
                        confluence_score: trade.confluence_score || trade.score || 0,
                        setup_score: trade.setup_score,
                        liquidity_sweep: trade.liquidity_sweep,
                        market_structure_shift: trade.market_structure_shift,
                        higher_timeframe_bias: trade.higher_timeframe_bias,
                        session_bias: trade.session_bias,
                        displacement: trade.displacement,
                        premium_discount: trade.premium_discount,
                        spread_safety: trade.spread_safety,
                    };

                    card.addEventListener('click', () => this.showSignalDetails(signalDetails));
                    grid.appendChild(card);
                });
            } else {
                this.renderRadarSummary([]);
                emptyState.style.display = "block";
                grid.style.display = "none";
            }
        } catch (e) {
            console.error("Global Radar error:", e);
            document.getElementById("globalRadarEmpty").style.display = "block";
            document.getElementById("globalRadarGrid").style.display = "none";
        }
    }

    getRadarFilteredTrades() {
        const trades = this.radarTrades || [];
        if (this.radarFilter === 'ALL') return trades;
        return trades.filter((trade) => (trade.trade_horizon?.type || 'INTRADAY') === this.radarFilter);
    }

    changeRadarPage(delta) {
        const totalPages = Math.max(1, Math.ceil(this.getRadarFilteredTrades().length / this.radarPageSize));
        this.radarPage = Math.min(totalPages, Math.max(1, this.radarPage + delta));
        this.renderGlobalRadar();
    }

    renderGlobalRadar() {
        const grid = document.getElementById("globalRadarGrid");
        const emptyState = document.getElementById("globalRadarEmpty");
        if (!grid || !emptyState) return;

        const filtered = this.getRadarFilteredTrades();
        const totalPages = Math.max(1, Math.ceil(filtered.length / this.radarPageSize));
        this.radarPage = Math.min(totalPages, Math.max(1, this.radarPage));
        const start = (this.radarPage - 1) * this.radarPageSize;
        const pageTrades = filtered.slice(start, start + this.radarPageSize);

        const pageInfo = document.getElementById('radarPageInfo');
        if (pageInfo) pageInfo.textContent = `Page ${this.radarPage} / ${totalPages}`;
        const prev = document.getElementById('radarPrevBtn');
        const next = document.getElementById('radarNextBtn');
        if (prev) prev.disabled = this.radarPage <= 1;
        if (next) next.disabled = this.radarPage >= totalPages;

        grid.innerHTML = "";
        if (!pageTrades.length) {
            emptyState.style.display = "block";
            grid.style.display = "none";
            return;
        }

        emptyState.style.display = "none";
        grid.style.display = "grid";
        const topScore = Math.max(...filtered.map((trade) => this.getSignalQualityScore(trade)));
        pageTrades.forEach((trade) => grid.appendChild(this.buildRadarCard(trade, topScore)));
    }

    buildRadarCard(trade, topScore = null) {
        const card = document.createElement("div");
        const best = this.isBestSignal(trade, topScore);
        card.className = `watchlist-card${best ? ' best-signal-card' : ''}`;
        card.style.cursor = 'pointer';

        const convictionScore = trade.conviction_score || 0;
        const phase = trade.phase || "Monitoring";
        const actionNeeded = trade.action_needed || "Waiting";
        const trigger = trade.trigger || "Awaiting confirmation";
        const setupScore = trade.setup_score?.score || trade.confluence_score || 0;
        const setupGrade = trade.setup_score?.grade || '-';
        const archetype = trade.setup_score?.archetype || trade.setup_name || 'Setup';
        const horizon = trade.trade_horizon || {type: 'INTRADAY', hold_time: '30 min-4h', confidence: 0, reason: 'default management'};
        const components = trade.setup_score?.components || [];
        const passedComponents = components.filter((item) => item.passed).length;
        const spread = trade.spread_safety || trade.setup_score?.spread || {};
        const falseMove = trade.false_move || trade.setup_score?.false_move || {};
        const newsMove = trade.news_move || trade.setup_score?.news_move || {};
        const spreadSafe = spread.safe !== false;
        const spreadText = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)}p` : 'n/a';
        const method = this.getExecutionMethod(trade);
        const mtf = this.getMtfSummary(trade);
        const falseMoveLabel = falseMove.type && !['UNKNOWN', 'RANGE'].includes(falseMove.type)
            ? falseMove.type.replaceAll('_', ' ')
            : 'No trap';
        const newsLabel = newsMove.mode && newsMove.mode !== 'NORMAL'
            ? newsMove.mode.replaceAll('_', ' ')
            : 'Normal';
        const componentChips = components.slice(0, 4).map((item) => `
            <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                ${item.passed ? '✓' : '×'} ${item.label}
            </span>
        `).join('');

        let statusColor = "#6b7280";
        if (convictionScore >= 80) statusColor = "#10b981";
        else if (convictionScore >= 60) statusColor = "#f59e0b";
        else if (convictionScore >= 40) statusColor = "#ef4444";

        card.innerHTML = `
            <div class="watchlist-card-header">
                <div class="watchlist-card-symbol">${trade.symbol} ${best ? this.getBestSignalBadge({...trade, best_signal: true}) : ''}</div>
                <div class="watchlist-badge" style="background: ${statusColor}">${phase}</div>
            </div>
            <div class="watchlist-card-body">
                <div class="radar-horizon-row">
                    <span class="horizon-badge horizon-${String(horizon.type).toLowerCase()}">${horizon.type}</span>
                    <small>${horizon.hold_time || ''}</small>
                </div>
                <div class="watchlist-card-type">${trade.type || trade.nature || 'Signal'}</div>
                <div class="watchlist-card-setup">${archetype}</div>
                <div class="watchlist-card-conviction">
                    <div class="conviction-bar">
                        <div class="conviction-fill" style="width: ${convictionScore}%"></div>
                    </div>
                    <span>${convictionScore}% Conviction</span>
                </div>
                <div class="watchlist-card-zone">Zone: ${trade.entry ? trade.entry.toFixed(5) : 'N/A'}</div>
                <div class="watchlist-card-zone">Early Score: ${(setupScore * 100).toFixed(0)}% | Grade ${setupGrade}</div>
                <div class="radar-micro-row">
                    <span>${this.escapeHtml(method.asset)} ${this.escapeHtml(method.horizon)}</span>
                    <span class="${mtf.tone === 'fail' ? 'metric-loss' : mtf.tone === 'pass' ? 'metric-profit' : 'metric-neutral'}">${this.escapeHtml(mtf.label)}</span>
                </div>
                <div class="radar-micro-row">
                    <span>${passedComponents}/${components.length || 8} checks</span>
                    <span class="${spreadSafe ? 'metric-profit' : 'metric-loss'}">Spread ${spreadText}</span>
                </div>
                <div class="radar-micro-row">
                    <span class="${falseMove.safe === false ? 'metric-loss' : 'metric-neutral'}">${falseMoveLabel}</span>
                    <span class="${newsMove.safe === false ? 'metric-loss' : 'metric-profit'}">News ${newsLabel}</span>
                </div>
                <div class="setup-checklist">${componentChips || '<span>No component detail</span>'}</div>
                <div class="watchlist-card-trigger">${trigger}</div>
                <div class="watchlist-card-action">${actionNeeded}</div>
                <div class="signal-card-actions">
                    <button class="btn-small btn-reset signal-details-btn" type="button">Details</button>
                    ${this.buildExecuteButtonHtml(trade, 'radar-execute-btn')}
                </div>
            </div>
        `;

        const signalDetails = {...trade, conviction: trade.conviction_score || 0};
        card.querySelector('.signal-details-btn')?.addEventListener('click', (event) => {
            event.stopPropagation();
            this.showSignalDetails(signalDetails);
        });
        card.querySelector('.signal-execute-btn')?.addEventListener('click', (event) => {
            event.stopPropagation();
            this.executeSignal(signalDetails, null, 'payload');
        });
        card.addEventListener('click', () => this.showSignalDetails(signalDetails));
        return card;
    }

    renderRadarSummary(trades = []) {
        const summary = document.getElementById('radarSummary');
        if (!summary) return;
        const total = trades.length;
        const strong = trades.filter((trade) => ['A', 'B'].includes(trade.setup_score?.grade)).length;
        const spreadSafe = trades.filter((trade) => (trade.spread_safety || trade.setup_score?.spread || {}).safe !== false).length;
        const scalp = trades.filter((trade) => trade.trade_horizon?.type === 'SCALP').length;
        const swing = trades.filter((trade) => trade.trade_horizon?.type === 'SWING').length;
        const traps = trades.filter((trade) => {
            const falseMove = trade.false_move || trade.setup_score?.false_move || {};
            return falseMove.type && !['UNKNOWN', 'RANGE'].includes(falseMove.type);
        }).length;
        const newsWatch = trades.filter((trade) => {
            const newsMove = trade.news_move || trade.setup_score?.news_move || {};
            return newsMove.mode && newsMove.mode !== 'NORMAL';
        }).length;
        summary.innerHTML = `
            <span>Signals: <strong>${total}</strong></span>
            <span>A/B setups: <strong>${strong}</strong></span>
            <span>Spread safe: <strong>${spreadSafe}</strong></span>
            <span>Scalps: <strong>${scalp}</strong></span>
            <span>Swings: <strong>${swing}</strong></span>
            <span>Traps: <strong>${traps}</strong></span>
            <span>News watch: <strong>${newsWatch}</strong></span>
        `;
    }

    loadPendingOrders(data) {
        try {
            const tbody = document.querySelector("#pendingOrdersTable tbody");
            const emptyState = document.getElementById("pendingOrdersEmpty");
            tbody.innerHTML = "";

            const orders = (data.data && Array.isArray(data.data)) ? data.data : [];

            if (orders.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                orders.forEach((order) => {
                    const row = tbody.insertRow();
                    const rr = order.tp && order.sl && order.entry 
                        ? ((Math.abs(order.tp - order.entry) / Math.abs(order.entry - order.sl)) || 0).toFixed(1)
                        : "-";
                    
                    row.innerHTML = `
                        <td><strong>${order.symbol}</strong></td>
                        <td>
                            <span class="phase-badge" style="
                                background: ${order.action === 'BUY' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)'};
                                color: ${order.action === 'BUY' ? '#10b981' : '#ef4444'};
                            ">${order.action}</span>
                        </td>
                        <td>${order.entry ? order.entry.toFixed(5) : '-'}</td>
                        <td>${order.sl ? order.sl.toFixed(5) : '-'}</td>
                        <td>${order.tp ? order.tp.toFixed(5) : '-'}</td>
                        <td>
                            <span class="score-high">${order.probability ? (order.probability * 100).toFixed(0) + '%' : 'N/A'}</span>
                        </td>
                        <td><span class="badge-pending">${order.ticket ? 'ACTIVE' : 'PENDING'}</span></td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Pending orders error:", e);
        }
    }

    loadWatchlist(data) {
        try {
            const tbody = document.querySelector("#watchlistTable tbody");
            const emptyState = document.getElementById("watchlistEmpty");
            tbody.innerHTML = "";

            const watchlist = (data.data && data.data.watchlist) ? data.data.watchlist : [];

            if (watchlist.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                watchlist.forEach((entry) => {
                    const row = tbody.insertRow();
                    const phase = entry.phase || 1;
                    const phaseClass = entry.ready_for_execution ? 'phase-ready' : `phase-${phase}`;
                    const phaseText = entry.ready_for_execution ? '✓ READY' : `Phase ${phase}/3`;
                    
                    const progress = (phase / 3) * 100;
                    
                    row.innerHTML = `
                        <td><strong>${entry.symbol}</strong></td>
                        <td>
                            <span class="phase-badge ${phaseClass}">${phaseText}</span>
                        </td>
                        <td>
                            <i class="fas ${entry.sweep_detected ? 'fa-check' : 'fa-hourglass-half'}"
                               style="color: ${entry.sweep_detected ? '#10b981' : '#fbbf24'}"></i>
                        </td>
                        <td>
                            <i class="fas ${entry.mBOS_detected ? 'fa-check' : 'fa-hourglass-half'}"
                               style="color: ${entry.mBOS_detected ? '#10b981' : '#fbbf24'}"></i>
                        </td>
                        <td>
                            <i class="fas ${entry.ready_for_execution ? 'fa-check' : 'fa-times'}"
                               style="color: ${entry.ready_for_execution ? '#10b981' : '#cbd5e1'}"></i>
                        </td>
                        <td>
                            <div class="phase-progress">
                                <div class="phase-progress-bar" style="width: ${progress}%"></div>
                            </div>
                        </td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Watchlist error:", e);
        }
    }

    loadPredictedZones(data) {
        try {
            const tbody = document.querySelector("#predictedZonesTable tbody");
            const emptyState = document.getElementById("zonesEmpty");
            tbody.innerHTML = "";

            const ready = (data.data && data.data.ready_for_execution) ? data.data.ready_for_execution : [];

            if (ready.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                ready.forEach((zone, idx) => {
                    const fvg = zone.extreme_fvg || {};
                    const rr = fvg.tp && fvg.sl && fvg.entry 
                        ? (Math.abs(fvg.tp - fvg.entry) / Math.abs(fvg.entry - fvg.sl)).toFixed(1)
                        : "-";
                    
                    const action = fvg.action || "UNKNOWN";
                    const actionColor = action === "BUY" ? "#10b981" : "#ef4444";
                    
                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td><strong>${zone.symbol}</strong></td>
                        <td>
                            <span style="color: ${actionColor}; font-weight: 600;">${action}</span>
                        </td>
                        <td>
                            <strong>${fvg.entry ? fvg.entry.toFixed(5) : '-'}</strong>
                            <div style="font-size: 11px; color: rgba(203, 213, 225, 0.6);">
                                ±${fvg.gap_size ? (fvg.gap_size * 0.5).toFixed(5) : '0'}
                            </div>
                        </td>
                        <td>${fvg.sl ? fvg.sl.toFixed(5) : '-'}</td>
                        <td>${fvg.tp ? fvg.tp.toFixed(5) : '-'}</td>
                        <td><span class="score-high">1:${rr}</span></td>
                        <td><span class="score-high">HIGH</span></td>
                        <td>
                            <button class="btn-small btn-place" onclick="bot.placeZoneOrder('${zone.symbol}')">
                                <i class="fas fa-play"></i> Execute
                            </button>
                        </td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Predicted zones error:", e);
        }
    }

    async placeZoneOrder(symbol) {
        try {
            const res = await fetch(`${this.apiBase}/pending-orders/place`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({symbols: [symbol]})
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`Order placed for ${symbol}!`);
                this.loadFutureTrades();
            } else {
                alert('Error: ' + (data.message || 'unknown'));
            }
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    async togglePause() {
        const btn = document.getElementById('pauseBtn');
        if (!btn) return;

        const isPaused = btn.textContent.includes('Resume');
        try {
            const res = await fetch(`${this.apiBase}/bot/${isPaused ? 'start' : 'stop'}`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.status === 'success') {
                btn.innerHTML = isPaused ?
                    '<i class="fas fa-pause"></i> Pause Scan' :
                    '<i class="fas fa-play"></i> Resume Scan';
                this.updateDashboard();
            }
        } catch (e) {
            console.error('Toggle pause error:', e);
        }
    }

    async approveSignal() {
        // This would need to be implemented based on current signal
        alert('Signal approval feature - to be implemented');
    }

    async rejectSignal() {
        // This would need to be implemented based on current signal
        alert('Signal rejection feature - to be implemented');
    }

}

document.addEventListener('DOMContentLoaded', () => {
    const bot = new Bot();
    window.bot = bot;
});

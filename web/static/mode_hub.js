const guestKey = 'tt_guest_id';
const hub = document.body.dataset.modeHub;
let hubProfile = null;
let queuePoll = null;
let activeQueueSports = [];

const PLAYOFF_OPTIONS = {
  baseball: [
    ['random', 'Random'], ['sunset_kingdom', 'Sunset Kingdom: 3 Japanese Players'], ['havana_heat', 'Havana Heat: 3 Cuban Players'],
    ['maple_corridor', 'Maple Corridor: 4 Canadian Players'], ['mvp_circle', 'MVP Circle: 2 MVP Winners'], ['young_buck', 'Young Buck: 2 Rookie of the Year Winners'],
    ['gonna_be_golden', 'Gonna Be Golden: 2 Gold Glove Winners'], ['secretariat', 'Secretariat: 1 Triple Crown Winner'], ['hound_dog', 'Hound-dog: 2 One-Franchise Players'],
    ['great_bambinos', 'Great Bambinos: 1 500-Home-Run Player'], ['ring_chaser', 'Ring Chaser: 15 Combined World Series Rings'], ['journeyman', 'Journeyman: 2 Seven-Team Players'],
  ],
  basketball: [
    ['random', 'Random'], ['bucket_getter', 'Bucket Getter: 2 Players with 25,000 Career Points'],
    ['season_scorer', 'Scoring Run: 2 Players with a 2,000-Point Season'],
    ['playmaker', 'Table Setter: 2 Players with 7,000 Career Assists'],
    ['three_point_club', 'Deep Range: 2 Players with 2,000 Career Three-Pointers'],
    ['ironhorse', 'Ironhorse: 2 Players with 1,000 Career Games'],
    ['one_team', 'Home Court: 2 Players with 8 Seasons for One Franchise'],
    ['journeyman', 'Frequent Flyer: 2 Players Who Played for 5 Teams'],
    ['mvp_circle', 'MVP Circle: 2 MVP Winners'],
    ['all_star_marathon', 'All-Star Marathon: 12 Combined All-Star Selections'],
    ['ring_chaser', 'Ring Chaser: 6 Combined Championships'],
    ['young_guns', 'Young Guns: 2 Rookie of the Year Winners'],
  ],
  football: [
    ['random', 'Random'], ['touchdown_club', 'End Zone: 2 Players with 100 Career Touchdowns'],
    ['season_scorer', 'Season Scorer: 2 Players with a 15-Touchdown Season'],
    ['air_raid', 'Air Raid: 2 Players with 300 Career Passing Touchdowns'],
    ['single_season_passer', 'Sunday Slingers: 2 Players with a 35-Passing-Touchdown Season'],
    ['sack_master', 'Sack Master: 2 Players with 100 Career Sacks'],
    ['ballhawk', 'Ballhawk: 2 Players with 30 Career Interceptions'],
    ['one_team', 'One Club: 2 Players with 10 Seasons for One Franchise'],
    ['journeyman', 'Journeyman: 2 Players Who Played for 5 Teams'],
    ['mvp_circle', 'MVP Circle: 2 MVP Winners'],
    ['pro_bowl_marathon', 'Pro Bowl Marathon: 12 Combined Pro Bowl Selections'],
    ['ring_chaser', 'Ring Chaser: 5 Combined Championships'],
    ['young_guns', 'Fresh Faces: 2 Rookie of the Year Winners'],
  ],
  hockey: [
    ['random', 'Random'], ['sniper', 'Sniper: 2 Players with 500 Career Goals'],
    ['single_season_sniper', 'Rocket Season: 1 Player with a 60-Goal Season'],
    ['playmaker', 'Playmaker: 2 Players with 1,000 Career Assists'],
    ['point_streak', 'Point Machine: 1 Player with a 120-Point Season'],
    ['one_team', 'Lifer: 2 Players with 10 Seasons for One Franchise'],
    ['journeyman', 'Journeyman: 2 Players Who Played for 5 Teams'],
    ['mvp_circle', 'Hart Club: 2 Hart Trophy Winners'],
    ['all_star_marathon', 'All-Star Marathon: 12 Combined All-Star Selections'],
    ['ironhorse', 'Ironhorse: 2 Players with 1,200 Career Games'],
    ['ring_chaser', 'Cup Chasers: 7 Combined Stanley Cup Credits'],
    ['young_guns', 'Fresh Ice: 2 Calder Trophy Winners'],
  ],
};

const MODE_RULES = {
  manager: {
    className: 'mode-manager',
    title: 'Manager Mode',
    body: [
      'Start with the Daily Starter and Name a TeamMate before the Clock Runs Out.',
      'Every Correct Answer becomes the New Top Player. Team-Seasons collect Strikes as the Lineup grows.',
      '<strong>Goal:</strong> Build Your Longest Lineup.',
      '<strong>Lose:</strong> Run Out of Time.',
    ],
  },
  film: {
    className: 'mode-film',
    title: 'Film Review',
    body: [
      'Solve the Daily Lineup by Naming the Team and Season that Connect each Pair of Players.',
      'A Correct Team and Season advances the Lineup. One Correct Field is a Partial Miss. Two Partial Misses in a Row count as a Miss.',
      '<strong>Goal:</strong> Complete Every TeamMate Link.',
      '<strong>Lose:</strong> Miss 3 Links.',
    ],
  },
  division: {
    className: 'mode-division',
    title: 'Division Rivalry',
    body: [
      'Two Players Take Turns Adding TeamMates to One Shared Lineup.',
      'A Team-Season with 3 Strikes is out. You cannot use a Player who only connects through an out Team-Season.',
      '<strong>Goal:</strong> Outlast Your Opponent.',
      '<strong>Win:</strong> Your Opponent Runs Out of Time.',
    ],
  },
  playoffs: {
    className: 'mode-playoffs',
    title: 'Playoffs',
    body: [
      'Play a Head-to-Head Lineup with Powerups and a Selected Win Condition.',
      'Each Player Gets 1 Use of Every Powerup. Powerups can Extend the Clock, Pressure the Opponent, or Open Special Same-Franchise Links.',
      '<strong>Goal:</strong> Complete Your Win Condition First, or Outlast Your Opponent.',
      '<strong>Win:</strong> Your Opponent Runs Out of Time or You Finish Your Win Condition.',
    ],
  },
};

const SHARED_RULES_HTML = `
  <div class="rules-sheet">
    <section class="rules-section">
      <h3>GameModes</h3>
      <div class="rules-mode-grid">
        <article class="rules-mode-card mode-manager">
          <h4>Manager Mode</h4>
          <p>Starting with the Leadoff Player, name TeamMates of the Top Player until time runs out.</p>
          <p>The same Team-Season Link used 3 times in a Lineup results in a Strike Out.</p>
          <p><strong>Goal:</strong> Set Your Longest Lineup.</p>
          <p><strong>Lose:</strong> Run Out of Time.</p>
        </article>
        <article class="rules-mode-card mode-film">
          <h4>Film Review</h4>
          <p>Build your daily Lineup by naming the team and season two TeamMates played together.</p>
          <p><strong>Goal:</strong> Complete Every TeamMate Link in the Lineup. Maintain Your Daily Streak or Try any You Missed in the Archives.</p>
          <p><strong>Lose:</strong> Get 3 Strikes. Consecutive Fouls Yield a Strike.</p>
        </article>
        <article class="rules-mode-card mode-division">
          <h4>Division Rivalry</h4>
          <p>Head-to-head, back-and-forth naming TeamMates of the Top Player and avoiding Struck Out Team-Seasons.</p>
          <p><strong>Goal:</strong> Outlast Your Opponent.</p>
          <p><strong>Lose:</strong> Run Out of Eligible TeamMates to Name.</p>
        </article>
        <article class="rules-mode-card mode-playoffs">
          <h4>Playoffs</h4>
          <p>Division Rivalry with Powerups and Win Conditions.</p>
          <p><strong>Goal:</strong> Complete Your Win Condition Before Your Opponent, or Outlast Them.</p>
          <p><strong>Lose:</strong> Allow Your Opponent to Play Their Win Condition, or Run Out of Time.</p>
        </article>
      </div>
    </section>
    <section class="rules-section rules-vocab-section">
      <h3>Vocabulary</h3>
      <div class="rules-vocab-list">
        <div class="rules-vocab-row core"><strong>Lineup</strong><span>The Player Chain. Ex: Anthony Rizzo -> Kris Bryant -> Javier Baez -> Aroldis Chapman. 2016 Cubs Struck Out.</span></div>
        <div class="rules-vocab-row core"><strong>TeamMate Link</strong><span>Two Players Connect if they Ever Played Together. Example: Rizzo and Bryant Link for Cubs 2015-2021.</span></div>
        <div class="rules-vocab-row core"><strong>Team-Season</strong><span>One Season for One Team. Example: 2016 Cubs or 2019-20 Lakers.</span></div>
        <div class="rules-vocab-row limits"><strong>Team Strikes</strong><span>When TeamMates are Linked, Team-Seasons get a Strike. 3 Strikes and TeamMates from that Team-Season can No Longer be Played.</span></div>
        <div class="rules-vocab-row limits"><strong>Blocked Guess</strong><span>If a Player Links to a TeamMate on a Struck Out Team, then they must Enter a New Eligible TeamMate.</span></div>
        <div class="rules-vocab-row playoffs"><strong>Powerup</strong><span>Only in Playoffs. Can get you Out of Tricky Links or Apply Pressure to your Opponent. Visit the Ref Button for Details.</span></div>
        <div class="rules-vocab-row playoffs"><strong>Win Condition</strong><span>Only in Playoffs. Name Enough Players that Qualify for your Win Condition Thresholds: Awards, Stats, or Teams. Visit the Ref Button for Details.</span></div>
      </div>
    </section>
    <section class="rules-section">
      <h3>Sport Terms</h3>
      <p class="rules-note">Rules use baseball terms first. Other sports use the same rules with different words.</p>
      <table class="rules-term-table">
        <thead><tr><th>Baseball</th><th>Basketball</th><th>Football</th><th>Hockey</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr><td>Leadoff</td><td>Tipoff</td><td>Snapper</td><td>Faceoff</td><td>The First Player in a Lineup.</td></tr>
          <tr><td>Struck Out</td><td>Fouled Out</td><td>Punted</td><td>Game Misconduct</td><td>The Same Team-Season used 3 Times. TeamMates from that Team-Season can no longer be used.</td></tr>
          <tr><td>Hit</td><td>Bucket</td><td>Completion</td><td>Goal</td><td>Correct Film Review Link.</td></tr>
          <tr><td>Foul</td><td>Rim Out</td><td>Tipped Pass</td><td>Offside</td><td>Correct Film Review Team OR Year. 2 in a row is a Miss.</td></tr>
          <tr><td>Strike</td><td>Foul</td><td>Turnover</td><td>Penalty</td><td>Missed Film Review Link. 3 Misses and you're Benched.</td></tr>
        </tbody>
      </table>
    </section>
  </div>`;

const POWERUP_REFERENCE = {
  baseball: [['Bubblegum', 'Name a Player from the same franchise with a 40+ home run season. +5 seconds.'], ['Pine Tar', 'Name a Player from the same franchise with a 200+ strikeout season. +5 seconds.'], ['Bat Donut', 'Name a Silver Slugger from the same franchise. +5 seconds.'], ['Sunglasses', 'Name an All-Star from the same franchise. +5 seconds.'], ['Backup Mitt', 'Name a Gold-Glover from the same franchise. +5 seconds.'], ['ABS', '+15 seconds.'], ['Quick Pitch', 'Your opponent only has 10 seconds on their next turn.']],
  basketball: [['Heat Check', 'Name a Player from the same franchise with a 2,000+ point season. +5 seconds.'], ['Sixth Man', 'Name a Player from the same franchise with 7,000+ career assists. +5 seconds.'], ['Switch', 'Name a Player from the same franchise who played the same position. +5 seconds.'], ['MVP Badge', 'Name a Player from the same franchise who won an MVP Award. +5 seconds.'], ['Star Power', 'Name an All-Star from the same franchise. +5 seconds.'], ['Timeout', '+15 seconds.'], ['Full-Court Press', 'Your opponent only has 10 seconds on their next turn.']],
  football: [['Trick Play', 'Name a Player from the same franchise with a 20+ touchdown season (non-passing). +5 seconds.'], ['Iron Man', 'Name a Player from the same franchise with 100 career games played. +5 seconds.'], ['Package Change', 'Name a Player from the same franchise who played the same position. +5 seconds.'], ['MVP Badge', 'Name a Player from the same franchise who won an MVP Award. +5 seconds.'], ['Bowler', 'Name a Pro Bowler from the same franchise. +5 seconds.'], ['Timeout', '+15 seconds.'], ['Blitz', 'Your opponent only has 10 seconds on their next turn.']],
  hockey: [['Breakaway', 'Name a Player from the same franchise with a 400+ goal career. +5 seconds.'], ['Veteran Presence', 'Name a Player from the same franchise with 800+ career points. +5 seconds.'], ['Line Change', 'Name a Player from the same franchise who played the same position. +5 seconds.'], ['Hart Honor', 'Name a Hart Trophy winner from the same franchise. +5 seconds.'], ['All-Star', 'Name an All-Star from the same franchise. +5 seconds.'], ['Timeout', '+15 seconds.'], ['Forecheck', 'Your opponent only has 10 seconds on their next turn.']],
};

const POWERUP_META = {
  bubblegum: ['BG', 'bubblegum'], pine_tar: ['PT', 'pine-tar'], bat_donut: ['BD', 'bat-donut'],
  sunglasses: ['SG', 'sunglasses'], backup_mitt: ['BM', 'backup-mitt'], abs: ['ABS', 'abs'],
  quick_pitch: ['QP', 'quick-pitch'], heat_check: ['HC', 'bubblegum'], sixth_man: ['6M', 'pine-tar'],
  switch: ['SW', 'bat-donut'], mvp_badge: ['MV', 'sunglasses'], all_star_callup: ['AS', 'backup-mitt'],
  timeout: ['TO', 'abs'], full_court_press: ['FP', 'quick-pitch'], trick_play: ['TP', 'bubblegum'],
  iron_man: ['IM', 'pine-tar'], package_change: ['PC', 'bat-donut'], pro_bowl_callup: ['PB', 'backup-mitt'],
  blitz: ['BZ', 'quick-pitch'], breakaway: ['BA', 'bubblegum'], veteran_presence: ['VP', 'pine-tar'],
  line_change: ['LC', 'bat-donut'], hart_honor: ['HT', 'sunglasses'], forecheck: ['FC', 'quick-pitch'],
};

const POWERUP_SVG = {
  bubblegum: '<path d="M8.2 8h7.6v8H8.2z"/><path d="m8.2 9.2-4-1.7 1.1 4.5-1.1 4.5 4-1.7"/><path d="m15.8 9.2 4-1.7-1.1 4.5 1.1 4.5-4-1.7"/><path d="M10.2 10.5h3.6M10.2 13.5h3.6"/>',
  pine_tar: '<path d="M12 3v18"/><path d="m12 4-5 6h10z"/><path d="m12 8-6 7h12z"/><path d="m12 12-7 7h14z"/><path d="M9 21h6"/>',
  bat_donut: '<path d="M6.2 19.2 17 8.4"/><path d="m16.2 5.8 2 2c.5.5.5 1.2 0 1.7l-.7.7-3.7-3.7.7-.7c.5-.5 1.2-.5 1.7 0z"/><path d="M5.1 18.1 7.3 20.3"/><circle cx="11.6" cy="13.8" r="2.6"/><circle cx="11.6" cy="13.8" r="1.1"/>',
  sunglasses: '<path d="M3.5 10.5h17"/><path d="M5 10.5h5l-1 4H6zM14 10.5h5l-1 4h-3z"/><path d="M10 11.3c1.2-.8 2.8-.8 4 0"/>',
  backup_mitt: '<path d="M6.5 19.5c-1.2-2.6-1.6-5.8-.9-9.7.3-1.7 1.5-2.1 2.5-.8l.5.8V5.9c0-1 .7-1.7 1.6-1.7.9 0 1.6.7 1.6 1.7v3.4l.7-2.4c.3-.9 1-1.4 1.8-1.1.8.2 1.2 1 1 1.9l-.6 2.4 1.3-1.8c.6-.8 1.5-.9 2.2-.4.7.5.8 1.4.2 2.2l-4.3 6.2c-1.3 1.9-3.7 2.9-7.6 3.2z"/><path d="M7.6 13.1c2.2 1.5 4.8 1.7 7.7.7"/><path d="M9.3 16.6c1.6.5 3.1.4 4.6-.3"/>',
  abs: '<rect x="5" y="4" width="14" height="16" rx="1.4"/><path d="M5 9.3h14M5 14.7h14"/><path d="M9.7 4v16M14.3 4v16"/>',
  quick_pitch: '<path d="M12 4 19 9v8l-7 3-7-3V9z"/><path d="M8.2 10h7.6M8.2 14h7.6"/>',
  heat_check: '<path d="M12 21c4-2.2 6-5 6-8.4 0-2.1-1-4-2.8-5.4.1 2-.7 3.2-2 4.1.4-3.2-.8-5.7-3.6-7.7.2 2.8-.6 4.4-2 5.9C6.5 10.7 6 12 6 13.6 6 16.7 8 19.2 12 21z"/><path d="M11 18c1.9-1.2 2.8-2.7 2.6-4.5-.7.8-1.4 1.1-2.2 1 .2-1.4-.2-2.7-1.3-3.9-.1 1.3-.5 2.2-1.2 2.9-.5.6-.8 1.3-.8 2.1 0 1.1 1 2 2.9 2.4z"/>',
  sixth_man: '<path d="M6 20V6h12v14"/><path d="M8.5 20v-8h7v8"/><path d="M9 9h2M13 9h2"/><path d="M11 16h2"/><path d="M6 6l2-2h8l2 2"/>',
  switch: '<path d="M7.2 8.4A6 6 0 0 1 17 7.2"/><path d="m17.2 4.8-.2 2.5-2.5-.2"/><path d="M16.8 15.6A6 6 0 0 1 7 16.8"/><path d="m6.8 19.2.2-2.5 2.5.2"/><path d="M9.5 12h5"/>',
  mvp_badge: '<circle cx="12" cy="9" r="4"/><path d="M9.5 12.4 8 20l4-2 4 2-1.5-7.6"/><path d="m12 6.6.7 1.5 1.6.2-1.2 1.1.3 1.6-1.4-.8-1.4.8.3-1.6-1.2-1.1 1.6-.2z"/>',
  all_star_callup: '<path d="m12 3 2.5 5.1 5.6.8-4 3.9.9 5.5-5-2.6-5 2.6.9-5.5-4-3.9 5.6-.8z"/>',
  timeout: '<circle cx="12" cy="12" r="7.5"/><path d="M12 7.5v5l3.2 2"/><path d="M8 3h8"/>',
  full_court_press: '<rect x="4" y="5" width="16" height="14" rx="1.5"/><path d="M12 5v14"/><circle cx="12" cy="12" r="2.2"/><path d="M4 9h3.4v6H4M20 9h-3.4v6H20"/><path d="M6.8 7.5h2.2M15 16.5h2.2"/>',
  trick_play: '<path d="M5 17c4-7 8-7 13-7"/><path d="m15.8 7.4 2.7 2.6-2.7 2.6"/><path d="m7 6.2.5 1 .9.4-.9.4-.5 1-.5-1-.9-.4.9-.4z"/><path d="m11 14 .5 1 .9.4-.9.4-.5 1-.5-1-.9-.4.9-.4z"/><path d="m14 5 .4.8.8.3-.8.3-.4.8-.4-.8-.8-.3.8-.3z"/>',
  iron_man: '<path d="M12 3 19 6v5.5c0 4-2.7 7.2-7 9.5-4.3-2.3-7-5.5-7-9.5V6z"/><path d="M9 12h6M12 9v6"/>',
  package_change: '<circle cx="7.5" cy="8" r="2.2"/><path d="M14.5 6.2h4v3.6h-4z"/><path d="M6 15.8h4v3.6H6z"/><circle cx="16.5" cy="17.6" r="2.2"/><path d="M10 8h3.2M13.2 8l-1.4-1.4M13.2 8l-1.4 1.4"/><path d="M14 17.6h-3.2M10.8 17.6l1.4-1.4M10.8 17.6l1.4 1.4"/>',
  pro_bowl_callup: '<path d="m12 3 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z"/><path d="M8 20h8"/>',
  blitz: '<path d="M13 2 5.5 13H12l-1 9 7.5-11H12z"/>',
  breakaway: '<ellipse cx="14" cy="13.5" rx="4.6" ry="2.4"/><path d="M4 8.2h7M3 12.2h8M5 16.2h6"/><path d="M10.4 13.5c1.3-1.5 3-2.1 5.1-1.8"/><path d="M17.8 11.8l2.3-1.3M18.4 14.4l2.6.7"/>',
  veteran_presence: '<path d="M12 3 19 6v5.5c0 4-2.7 7.2-7 9.5-4.3-2.3-7-5.5-7-9.5V6z"/><path d="m8.8 12 2.1 2.1 4.5-5"/>',
  line_change: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="1.8"/><path d="M4.5 12h15"/><path d="M12 4.5v15"/>',
  hart_honor: '<path d="M12 20s-7-4.2-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.8-7 10-7 10z"/><path d="m12 8.2.8 1.6 1.8.3-1.3 1.2.3 1.8-1.6-.8-1.6.8.3-1.8-1.3-1.2 1.8-.3z"/>',
  forecheck: '<path d="M5 18 18 5"/><path d="M15.4 4.8h3.4v3.4"/><path d="M6.6 15.6l2.2 2.2"/><circle cx="8" cy="16" r="1.8"/><path d="M14.5 14.5c-2.2-1.5-4.5-1.4-6.9.3"/><path d="M4 10h4M3 13h3.2"/>',
};

function powerupKeyFromLabel(label) {
  const explicit = {
    'All-Star Call-Up': 'all_star_callup',
    'All-Star': 'all_star_callup',
    'Star Power': 'all_star_callup',
    'Pro Bowl Call-Up': 'pro_bowl_callup',
    'Bowler': 'pro_bowl_callup',
  };
  if (explicit[label]) return explicit[label];
  return String(label || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}

function powerupChip(label) {
  const key = powerupKeyFromLabel(label);
  const meta = POWERUP_META[key] || ['P', 'generic'];
  const icon = POWERUP_SVG[key]
    ? `<span class="powerup-icon powerup-svg" aria-hidden="true"><svg viewBox="0 0 24 24">${POWERUP_SVG[key]}</svg></span>`
    : `<span class="powerup-icon">${escapeHtml(meta[0])}</span>`;
  return `<div class="reference-chip powerup-chip powerup-chip-static powerup-${meta[1]}">
    ${icon}
    <span class="powerup-label">${escapeHtml(label)}</span>
  </div>`;
}

const CONDITION_REQUIREMENTS = {
  random: 'Randomly choose from available conditions.',
  sunset_kingdom: 'Japanese players.', havana_heat: 'Cuban players.', maple_corridor: 'Canadian players.',
  mvp_circle: 'MVP winners.', young_buck: 'Rookie of the Year winners.', gonna_be_golden: 'Gold Glove winners.',
  secretariat: 'Triple Crown winner.', hound_dog: 'One-franchise lifers.', great_bambinos: '500 career home run player.',
  ring_chaser: 'Combined championships.', journeyman: 'Players with many franchises.',
  bucket_getter: 'Career scoring greats.', season_scorer: 'Peak single-season scorers.', playmaker: 'Career assist/playmaking greats.',
  three_point_club: 'Elite three-point shooters.', ironhorse: 'Durable long-career players.', one_team: 'One-franchise players.',
  all_star_marathon: 'Combined All-Star selections.', young_guns: 'Rookie of the Year winners.', touchdown_club: 'Career touchdown scorers.',
  air_raid: 'Career passing leaders.', single_season_passer: 'Peak passing seasons.', sack_master: 'Career sack leaders.',
  ballhawk: 'Career interception leaders.', pro_bowl_marathon: 'Combined Pro Bowl selections.', sniper: 'Career goal scorers.',
  single_season_sniper: 'Peak goal seasons.', point_streak: 'Career point producers.',
};

function gameCopyStyle(text) {
  return String(text || '')
    .replace(/\bplayers\b/g, 'Players')
    .replace(/\bplayer\b/g, 'Player')
    .replace(/\bopponent\b/g, 'Opponent')
    .replace(/\bpowerups\b/g, 'Powerups')
    .replace(/\bpowerup\b/g, 'Powerup')
    .replace(/\bteam-seasons\b/g, 'Team-Seasons')
    .replace(/\bteam-season\b/g, 'Team-Season')
    .replace(/\blineup\b/g, 'Lineup')
    .replace(/\bconditions\b/g, 'Conditions')
    .replace(/\bcondition\b/g, 'Condition')
    .replace(/\bcareer\b/g, 'Career')
    .replace(/\bseason\b/g, 'Season')
    .replace(/\bfranchise\b/g, 'Franchise')
    .replace(/\bsame\b/g, 'Same')
    .replace(/\bposition\b/g, 'Position')
    .replace(/\bcombined\b/g, 'Combined')
    .replace(/\bchampionships\b/g, 'Championships')
    .replace(/\bselections\b/g, 'Selections')
    .replace(/\bavailable\b/g, 'Available');
}

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return response.json();
}

async function ensureHubGuestId() {
  const stored = localStorage.getItem(guestKey) || '';
  if (stored) {
    hubProfile = { guest_id: stored };
    post('/api/profile/bootstrap', { guest_id: stored }).then((profile) => {
      if (profile?.guest_id) {
        hubProfile = profile;
        localStorage.setItem(guestKey, profile.guest_id);
      }
    }).catch(() => {});
    return stored;
  }
  hubProfile = await post('/api/profile/bootstrap', { guest_id: '' });
  if (!hubProfile?.guest_id) return '';
  localStorage.setItem(guestKey, hubProfile.guest_id);
  return hubProfile.guest_id;
}

async function initHub() {
  const guestId = await ensureHubGuestId();
  if (!guestId) return;
  if (hub === 'manager') {
    const tiles = await post('/api/manager/tiles', { guest_id: guestId });
    renderManagerTiles(tiles);
    post('/api/manager/summary', { guest_id: guestId }).then(renderManagerSummary).catch(() => {});
  }
  if (hub === 'film') {
    const summary = await post('/api/film/archive_summary', { guest_id: guestId });
    Object.entries(summary.sports || {}).forEach(([sport, data]) => renderFilmReviewHubSport(sport, data || {}));
  }
  configureSharedQueue();
}
initHub();

function displayStatus(status) {
  const normalized = String(status || 'unseen');
  if (normalized === 'won') return 'Fully Scouted';
  if (normalized === 'lost') return 'Benched';
  if (normalized === 'new') return 'New';
  if (normalized === 'in_progress') return 'In Progress';
  return 'Unseen';
}

function formatArchiveLabel(day) {
  const dateText = day.date ? new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }).replace(', 20', ", '") : '';
  const status = displayStatus(day.is_today && day.status === 'unseen' ? 'new' : day.status);
  const prefix = day.is_today ? 'Today' : `#${day.number}`;
  return `${prefix}${dateText ? ' - ' + dateText : ''} - ${status}`;
}

function filmReviewUrl(sport, day) {
  const routeSport = String(sport || '').split(':')[0];
  const unit = day.unit ? `unit=${encodeURIComponent(day.unit)}` : '';
  const archive = day.is_today ? '' : `date=${encodeURIComponent(day.date)}&archive=1`;
  const query = [unit, archive].filter(Boolean).join('&');
  return `/film/${routeSport}${query ? '?' + query : ''}`;
}

function unitLabel(unit) {
  if (unit === 'offense') return 'Offense';
  if (unit === 'defense') return 'Defense';
  return '';
}

function renderFilmReviewArchiveSelect(sport, days, unitData = null) {
  const select = document.querySelector(`[data-fr-archive-select="${sport}"]`);
  const status = document.querySelector(`[data-fr-today-status="${sport}"]`);
  if (!select) return;
  const current = days.find((day) => day.is_today) || days[0];
  if (status && current) {
    const streak = Number(unitData?.streak || 0);
    const display = current.is_today && current.status === 'unseen' ? 'new' : current.status || 'unseen';
    status.textContent = `Streak ${streak} | ${displayStatus(display)}`;
    status.className = `sport-tile-status fr-today-status ${display}`;
  }
  const sorted = [...days].sort((a, b) => {
    if (a.is_today) return -1;
    if (b.is_today) return 1;
    return Number(b.number || 0) - Number(a.number || 0);
  });
  select.innerHTML = '<option value="">Select Archived Tape...</option>' + sorted.map((day) => {
    const optionStatus = day.is_today && day.status === 'unseen' ? 'new' : day.status || 'unseen';
    return `<option value="${filmReviewUrl(sport, day)}" class="fr-option-${optionStatus}">${unitLabel(day.unit)}${day.unit ? ' - ' : ''}${formatArchiveLabel(day)}</option>`;
  }).join('');
  select.value = '';
  select.addEventListener('change', () => {
    if (select.value) window.location.href = select.value;
  });
}

function renderFilmPreview(sport, data) {
  const tile = document.querySelector(`[data-film-unit-tile="${sport}"]`) ||
    document.querySelector(`[data-sport="${sport}"]`);
  if (!tile) return;
  tile.querySelector('.film-hub-meta')?.remove();
  const preview = data.preview || [];
  const compact = preview.some((player) => String(player.name || '').length > 16);
  const meta = document.createElement('span');
  meta.className = `film-hub-meta ${compact ? 'compact-names' : ''}`;
  meta.innerHTML = `<span class="film-preview-pair">${preview.map((player) => `
      <span class="film-preview-player">
        <span class="film-preview-photo">${player.headshot_url ? `<img src="${escapeHtml(player.headshot_url)}" alt="" loading="lazy" decoding="async">` : ''}</span>
        <small>${escapeHtml(player.name || 'Unknown')}</small>
      </span>`).join('')}</span>`;
  tile.appendChild(meta);
}

function renderFilmReviewHubSport(sport, data) {
  if (sport === 'football') {
    ['offense', 'defense'].forEach((unit) => {
      const unitData = data.today?.[unit] || {};
      const unitDays = (data.days || []).filter((day) => day.unit === unit);
      renderFilmReviewArchiveSelect(`football:${unit}`, unitDays, unitData);
      renderFilmPreview(`football:${unit}`, unitData);
    });
    return;
  }
  const unitData = data.today?.default || {};
  renderFilmReviewArchiveSelect(sport, data.days || [], unitData);
  renderFilmPreview(sport, unitData);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function managerScore(run, emptyText = 'No Lineup Yet') {
  if (!run) return `<strong>--</strong><span>${emptyText}</span>`;
  const starter = run.starter?.name ? `Starter: ${escapeHtml(run.starter.name)}` : '';
  return `<strong>${run.chain_length}</strong><span>${starter}</span>`;
}

function renderManagerCard(title, run, emptyText) {
  return `<div class="manager-board-card">
    <span>${escapeHtml(title)}</span>
    ${managerScore(run, emptyText)}
    ${run?.display_name ? `<small>${escapeHtml(run.display_name)}</small>` : ''}
  </div>`;
}

function renderManagerLeaderboard(summary, sport) {
  const data = summary?.sports?.[sport] || {};
  const grid = document.getElementById('manager-leaderboard-grid');
  const records = document.getElementById('manager-records-select');
  if (!grid || !records) return;
  grid.innerHTML = [
    renderManagerCard('Your All-Time Best', data.own_all_time, 'No Finished Run'),
    renderManagerCard('Your Best Today', data.own_today, 'No Run Today'),
    renderManagerCard('Global All-Time Best', data.global_all_time, 'No Global Run'),
    renderManagerCard('Global Best Today', data.global_today, 'No Global Run Today'),
  ].join('');
  const rows = data.records || [];
  records.innerHTML = rows.length
    ? rows.map((row) => {
      const starter = row.starter?.name ? ` - ${row.starter.name}` : '';
      return `<option>${escapeHtml(row.date || '')} - ${escapeHtml(row.display_name || 'Guest')} - ${row.chain_length}${escapeHtml(starter)}</option>`;
    }).join('')
    : '<option>No Daily Records Yet</option>';
}

function renderManagerSummary(summary) {
  const sports = summary?.sports || {};
  renderManagerTiles(summary);
  const picker = document.getElementById('manager-leaderboard-sport');
  if (picker) {
    const saved = localStorage.getItem('tt_manager_board_sport') || 'baseball';
    picker.value = sports[saved] ? saved : 'baseball';
    renderManagerLeaderboard(summary, picker.value);
    if (!picker.dataset.bound) {
      picker.dataset.bound = 'true';
      picker.addEventListener('change', () => {
        localStorage.setItem('tt_manager_board_sport', picker.value);
        renderManagerLeaderboard(summary, picker.value);
      });
    }
  }
}

function renderManagerTiles(summary) {
  const sports = summary?.sports || {};
  Object.entries(sports).forEach(([sport, data]) => {
    const bestTarget = document.querySelector(`[data-manager-best="${sport}"]`);
    if (bestTarget) bestTarget.textContent = `Longest Lineup: ${data.own_best ?? data.own_all_time?.chain_length ?? 0}`;
    const starterTarget = document.querySelector(`[data-manager-starter="${sport}"]`);
    if (starterTarget) {
      const starter = data.starter || {};
      const photo = starter.headshot_url
        ? `<img src="${escapeHtml(starter.headshot_url)}" alt="">`
        : '';
      starterTarget.innerHTML = `<span class="manager-starter-photo ${photo ? '' : 'placeholder'}">${photo}</span>
        <span><small>Today's Starter</small><strong>${escapeHtml(starter.name || 'Unknown')}</strong></span>`;
    }
  });
}

function selectedSports() {
  return [...document.querySelectorAll('.shared-sport-option input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

function playoffPreferences() {
  const prefs = {};
  document.querySelectorAll('[data-condition-sport]').forEach((select) => {
    prefs[select.dataset.conditionSport] = select.value || 'random';
    localStorage.setItem('tt_hub_playoff_condition_' + select.dataset.conditionSport, prefs[select.dataset.conditionSport]);
  });
  return prefs;
}

function playoffPreferenceForSport(sport) {
  const select = document.querySelector(`[data-condition-sport="${sport}"]`);
  const value = select?.value || 'random';
  localStorage.setItem('tt_hub_playoff_condition_' + sport, value);
  return { [sport]: value };
}

function setQueueUi(searching, text) {
  const start = document.getElementById('shared-queue-btn');
  const cancel = document.getElementById('shared-cancel-btn');
  const status = document.getElementById('shared-queue-status');
  if (!start || !cancel || !status) return;
  start.disabled = searching;
  cancel.hidden = !searching;
  status.textContent = text || '';
}

function queueEndpoint(suffix) {
  return `/api/modes/${hub}/${suffix}`;
}

async function handleQueueResponse(response) {
  if (response.error) {
    setQueueUi(false, response.error);
    return;
  }
  if (response.status === 'matched' && response.redirect) {
    window.location.href = response.redirect;
    return;
  }
  setQueueUi(true, 'Searching Selected Sports...');
}

async function queueForSports(sports, preferences = {}) {
  clearInterval(queuePoll);
  activeQueueSports = [...sports];
  localStorage.setItem(`tt_multi_queue_${hub}`, JSON.stringify({ sports, preferences }));
  const response = await post(queueEndpoint('queue'), {
    guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    sports,
    preferences,
  });
  await handleQueueResponse(response);
  if (response.status !== 'matched' && !response.error) {
    queuePoll = setInterval(pollSharedQueue, 1000);
  }
}

async function pollSharedQueue() {
  const response = await post(queueEndpoint('status'), {
    guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    sports: activeQueueSports.length ? activeQueueSports : selectedSports(),
  });
  if (response.status === 'matched' && response.redirect) {
    window.location.href = response.redirect;
    return;
  }
  if (response.status === 'idle') {
    clearInterval(queuePoll);
    queuePoll = null;
    activeQueueSports = [];
    setQueueUi(false, 'Queue Stopped.');
  }
}

function configureSharedQueue() {
  const panel = document.getElementById('shared-queue-panel');
  if (!panel) return;
  document.querySelectorAll('[data-condition-sport]').forEach((select) => {
    const sport = select.dataset.conditionSport;
    const options = PLAYOFF_OPTIONS[sport] || [['random', 'Random']];
    select.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
    select.value = localStorage.getItem('tt_hub_playoff_condition_' + sport) || 'random';
  });
  document.getElementById('shared-queue-btn')?.addEventListener('click', async () => {
    const sports = selectedSports();
    if (!sports.length) {
      setQueueUi(false, 'Choose at Least One Sport.');
      return;
    }
    await queueForSports(sports, hub === 'playoffs' ? playoffPreferences() : {});
  });
  document.getElementById('shared-cancel-btn')?.addEventListener('click', async () => {
    clearInterval(queuePoll);
    queuePoll = null;
    activeQueueSports = [];
    await post(queueEndpoint('cancel_queue'), {
      guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    });
    setQueueUi(false, 'Queue Canceled.');
  });
}

function hubModal(title, html) {
  const modal = document.getElementById('hub-modal');
  const titleEl = document.getElementById('hub-modal-title');
  const textEl = document.getElementById('hub-modal-text');
  if (!modal || !titleEl || !textEl) return;
  titleEl.textContent = title;
  textEl.innerHTML = html;
  modal.hidden = false;
}

function closeHubModal() {
  const modal = document.getElementById('hub-modal');
  if (modal) modal.hidden = true;
}

function allModeRulesHtml() {
  const rule = MODE_RULES[hub];
  if (!rule) return SHARED_RULES_HTML;
  return `<div class="rules-sheet">
    <section class="rules-section">
      <div class="rules-mode-grid rules-mode-grid-single">
        <article class="rules-mode-card ${escapeHtml(rule.className)}">
          <h4>${escapeHtml(rule.title)}</h4>
          ${rule.body.map((line) => `<p>${line}</p>`).join('')}
        </article>
      </div>
    </section>
  </div>`;
}

function conditionsHtml(sport = null) {
  const sports = sport ? [sport] : ['baseball', 'basketball', 'football', 'hockey'];
  return sports.map((sportKey) => {
    const rows = PLAYOFF_OPTIONS[sportKey] || [];
    return `<h3>${escapeHtml(sportKey[0].toUpperCase() + sportKey.slice(1))}</h3>
      <table class="reference-table">
        <thead><tr><th>Condition</th><th>Need</th></tr></thead>
        <tbody>${rows.filter(([key]) => key !== 'random').map(([key, label]) => {
          const [name, need] = label.split(': ');
          return `<tr><td class="reference-condition-name">${escapeHtml(name)}</td><td class="reference-condition-need">${escapeHtml(gameCopyStyle(need || CONDITION_REQUIREMENTS[key] || 'Complete the Listed Stat Goal Before Your Opponent.'))}</td></tr>`;
        }
        ).join('')}</tbody>
      </table>`;
  }).join('');
}

function powerupsHtml(sport = null) {
  const sports = sport ? [sport] : ['baseball', 'basketball', 'football', 'hockey'];
  return sports.map((sportKey) => {
    const rows = POWERUP_REFERENCE[sportKey] || [];
    return `<h3>${escapeHtml(sportKey[0].toUpperCase() + sportKey.slice(1))}</h3><div class="reference-key">${rows.map(([label, desc]) =>
      `<div class="reference-row">${powerupChip(label)}<div class="reference-copy"><div class="muted small">${escapeHtml(gameCopyStyle(desc))}</div></div></div>`
    ).join('')}</div>`;
  }).join('');
}

document.getElementById('hub-exit-btn')?.addEventListener('click', () => {
  window.location.href = '/';
});
document.getElementById('hub-rules-btn')?.addEventListener('click', () => {
  hubModal('How to Play', allModeRulesHtml());
});
document.getElementById('hub-conditions-btn')?.addEventListener('click', () => {
  hubModal('Win Conditions', conditionsHtml());
});
document.getElementById('hub-powerups-btn')?.addEventListener('click', () => {
  hubModal('Powerups', powerupsHtml());
});
document.getElementById('hub-modal-close')?.addEventListener('click', closeHubModal);
document.getElementById('hub-modal-backdrop')?.addEventListener('click', closeHubModal);

(function (host) {
  'use strict';
  const periods = ['09:00–11:00', '11:00–13:00', '14:00–16:00'];
  const teachers = ['Dr. Rao', 'Dr. Chen', 'Prof. Lee'];
  const mounted = new WeakMap();
  function seed() {
    return {
      slotCount: 3,
      rooms: [
        { id: 'R1', name: 'Lecture room', capacity: 36, open: true },
        { id: 'R2', name: 'Seminar room', capacity: 24, open: true },
      ],
      courses: [
        {
          id: 'C1',
          name: 'Algorithms',
          teacher: 'Dr. Rao',
          cohort: 'Year 1',
          students: 30,
          preferred: 0,
          available: [true, true, true],
        },
        {
          id: 'C2',
          name: 'Physics',
          teacher: 'Dr. Chen',
          cohort: 'Year 1',
          students: 24,
          preferred: 1,
          available: [true, true, true],
        },
        {
          id: 'C3',
          name: 'Networks',
          teacher: 'Dr. Rao',
          cohort: 'Year 2',
          students: 28,
          preferred: 0,
          available: [true, true, true],
        },
        {
          id: 'C4',
          name: 'Design',
          teacher: 'Prof. Lee',
          cohort: 'Year 2',
          students: 20,
          preferred: 2,
          available: [true, true, true],
        },
        {
          id: 'C5',
          name: 'Statistics',
          teacher: 'Dr. Chen',
          cohort: 'Year 1',
          students: 22,
          preferred: 2,
          available: [true, true, true],
        },
      ],
    };
  }
  function solve(input) {
    if (
      !input ||
      !Number.isInteger(input.slotCount) ||
      input.slotCount < 1 ||
      input.slotCount > 3 ||
      !Array.isArray(input.rooms) ||
      input.rooms.length < 1 ||
      input.rooms.length > 3 ||
      !Array.isArray(input.courses) ||
      input.courses.length < 1 ||
      input.courses.length > 6
    )
      throw new Error('Use 1–3 periods, 1–3 rooms and at most six courses.');
    const roomIds = new Set(),
      courseIds = new Set();
    for (const room of input.rooms) {
      if (
        typeof room.id !== 'string' ||
        roomIds.has(room.id) ||
        !Number.isInteger(room.capacity) ||
        room.capacity < 1 ||
        room.capacity > 60 ||
        typeof room.open !== 'boolean'
      )
        throw new Error('Each room needs a unique ID, open/closed state and capacity of 1–60.');
      roomIds.add(room.id);
    }
    for (const course of input.courses) {
      if (
        typeof course.id !== 'string' ||
        courseIds.has(course.id) ||
        typeof course.teacher !== 'string' ||
        !course.teacher ||
        typeof course.cohort !== 'string' ||
        !course.cohort ||
        !Number.isInteger(course.students) ||
        course.students < 1 ||
        course.students > 60 ||
        !Number.isInteger(course.preferred) ||
        course.preferred < 0 ||
        course.preferred > 2 ||
        !Array.isArray(course.available) ||
        course.available.length !== 3 ||
        !course.available.every((v) => typeof v === 'boolean')
      )
        throw new Error(
          'Courses need unique IDs, teacher, cohort, 1–60 students and valid period preferences/availability.',
        );
      courseIds.add(course.id);
    }
    const rooms = input.rooms.filter((room) => room.open);
    const fail = (reason) => ({
      feasible: false,
      objective: null,
      assignments: [],
      examined: 0,
      exact: true,
      reason,
    });
    if (!rooms.length) return fail('No rooms are open. Open a room to schedule classes.');
    if (input.courses.length > rooms.length * input.slotCount)
      return fail('There are more classes than available room/period places. Open another room or add a period.');
    const choices = new Map();
    for (const course of input.courses) {
      const options = [];
      for (let slot = 0; slot < input.slotCount; slot++)
        for (const room of rooms) {
          if (course.available[slot] && room.capacity >= course.students)
            options.push({
              courseId: course.id,
              roomId: room.id,
              slot,
              penalty: Math.abs(slot - course.preferred),
              waste: room.capacity - course.students,
            });
        }
      options.sort(
        (a, b) =>
          a.penalty - b.penalty || a.waste - b.waste || a.slot - b.slot || a.roomId.localeCompare(b.roomId),
      );
      if (!options.length)
        return fail(
          course.name +
            ' has no available period with an open room large enough for ' +
            course.students +
            ' students.',
        );
      choices.set(course.id, options);
    }
    const courses = [...input.courses].sort(
      (a, b) => choices.get(a.id).length - choices.get(b.id).length || a.id.localeCompare(b.id),
    );
    let best = null,
      examined = 0;
    function search(index, assignments, penalty, waste) {
      examined++;
      if (best && (penalty > best.penalty || (penalty === best.penalty && waste >= best.waste))) return;
      if (index === courses.length) {
        best = { assignments, penalty, waste };
        return;
      }
      const course = courses[index];
      for (const option of choices.get(course.id)) {
        if (
          assignments.some((other) => {
            if (other.slot !== option.slot) return false;
            const previous = courses.find((item) => item.id === other.courseId);
            return (
              other.roomId === option.roomId ||
              previous.teacher === course.teacher ||
              previous.cohort === course.cohort
            );
          })
        )
          continue;
        search(index + 1, [...assignments, option], penalty + option.penalty, waste + option.waste);
      }
    }
    search(0, [], 0, 0);
    return best
      ? {
          feasible: true,
          objective: best.penalty,
          seatWaste: best.waste,
          assignments: best.assignments,
          examined,
          exact: true,
        }
      : {
          ...fail(
            'No timetable satisfies room capacity, course availability, teacher clashes and cohort clashes.',
          ),
          examined,
        };
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function numeric(value, label) {
    const node = el('input');
    Object.assign(node, { type: 'number', min: 1, max: 60, step: 1, value });
    node.setAttribute('aria-label', label);
    return node;
  }
  function select(options, value, label) {
    const node = el('select');
    node.setAttribute('aria-label', label);
    options.forEach((item) => {
      const option = el('option', '', item.label);
      option.value = item.value;
      node.append(option);
    });
    node.value = value;
    return node;
  }
  function metric(label, value) {
    const node = el('div', 'schedule-metric');
    node.append(el('span', '', label), el('strong', '', value));
    return node;
  }
  function mount(root, inputs) {
    mounted.get(root)?.();
    const state = inputs == null ? seed() : JSON.parse(JSON.stringify(inputs)),
      frame = el('div', 'scheduling-workbench');
    const output = el('section', 'problem-card schedule-output');
    output.setAttribute('aria-label', 'Optimized class timetable');
    const status = el('p', 'schedule-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const metrics = el('div', 'problem-metrics schedule-metrics'),
      figure = el('div', 'problem-figure schedule-table-scroll');
    output.append(el('h2', '', 'A timetable without clashes'), status, metrics, figure);
    const controls = el('section', 'problem-card schedule-inputs');
    controls.append(el('h2', '', 'Set rooms and classes'));
    const reset = el('button', 'secondary', 'Reset example');
    reset.type = 'button'; reset.onclick = () => mount(root); controls.append(reset);
    controls.append(
      el(
        'p',
        'schedule-note',
        'Schedule each class once. Teachers, student cohorts and rooms cannot overlap. Minimize total distance from preferred periods, then unused seats.',
      ),
    );
    const fields = el('div', 'problem-controls schedule-fields');
    const periodLabel = el('label', 'problem-field', 'Available periods');
    const slotCount = select(
      [1, 2, 3].map((n) => ({ value: n, label: n + (n === 1 ? ' period' : ' periods') })),
      state.slotCount,
      'Number of available class periods',
    );
    periodLabel.append(slotCount);
    fields.append(periodLabel);
    const roomEditors = state.rooms.map((room) => {
      const label = el('label', 'problem-field', room.name + ' seats');
      const capacity = numeric(room.capacity, room.name + ' capacity');
      label.append(capacity);
      fields.append(label);
      const openLabel = el('label', 'problem-field', room.name + ' availability');
      const open = select(
        [
          { value: 'true', label: 'Open' },
          { value: 'false', label: 'Closed' },
        ],
        String(room.open),
        room.name + ' availability',
      );
      openLabel.append(open);
      fields.append(openLabel);
      return { capacity, open };
    });
    controls.append(fields);
    const scroll = el('div', 'schedule-table-scroll'),
      table = el('table', 'problem-table schedule-input-table');
    table.append(el('caption', 'sr-only', 'Class sizes, teacher and cohort assignments, and time preferences'));
    const head = el('tr');
    for (const title of ['Class', 'Students', 'Teacher', 'Cohort', 'Preferred period', 'Available periods'])
      head.append(el('th', '', title));
    const thead = el('thead');
    thead.append(head);
    table.append(thead);
    const tbody = el('tbody');
    const courseEditors = state.courses.map((course) => {
      const row = el('tr');
      const name = el('th', '', course.name);
      name.scope = 'row';
      row.append(name);
      const students = numeric(course.students, course.name + ' students');
      const studentCell = el('td');
      studentCell.append(students);
      row.append(studentCell);
      const teacher = select(
        [...new Set([...teachers, ...state.courses.map(course => course.teacher)])].map((value) => ({ value, label: value })),
        course.teacher,
        course.name + ' teacher',
      );
      const cohort = select(
        [...new Set(['Year 1', 'Year 2', ...state.courses.map(course => course.cohort)])].map((value) => ({ value, label: value })),
        course.cohort,
        course.name + ' cohort',
      );
      const preferred = select(
        periods.map((label, value) => ({ value, label })),
        course.preferred,
        course.name + ' preferred period',
      );
      for (const control of [teacher, cohort, preferred]) {
        const cell = el('td');
        cell.append(control);
        row.append(cell);
      }
      const availabilityCell = el('td'),
        availability = el('div', 'schedule-availability');
      const available = periods.map((period, i) => {
        const label = el('label');
        const check = el('input');
        check.type = 'checkbox';
        check.checked = course.available[i];
        check.setAttribute('aria-label', course.name + ' available ' + period);
        label.append(check, document.createTextNode(['09', '11', '14'][i]));
        availability.append(label);
        return check;
      });
      availabilityCell.append(availability);
      row.append(availabilityCell);
      tbody.append(row);
      return { students, teacher, cohort, preferred, available };
    });
    table.append(tbody);
    scroll.append(table);
    controls.append(scroll);
    frame.append(output, controls);
    root.replaceChildren(frame);
    function read(node) {
      return node.value === '' ? NaN : Number(node.value);
    }
    function update() {
      metrics.replaceChildren();
      figure.replaceChildren();
      try {
        const input = {
          slotCount: Number(slotCount.value),
          rooms: state.rooms.map((room, i) => ({
            ...room,
            capacity: read(roomEditors[i].capacity),
            open: roomEditors[i].open.value === 'true',
          })),
          courses: state.courses.map((course, i) => ({
            ...course,
            students: read(courseEditors[i].students),
            teacher: courseEditors[i].teacher.value,
            cohort: courseEditors[i].cohort.value,
            preferred: Number(courseEditors[i].preferred.value),
            available: courseEditors[i].available.map((node) => node.checked),
          })),
        };
        const result = solve(input);
        status.className = 'schedule-status' + (result.feasible ? '' : ' schedule-error');
        status.textContent = result.feasible
          ? 'All ' +
            input.courses.length +
            ' classes scheduled. Exact minimum preference penalty with no room, teacher or cohort clashes.'
          : result.reason;
        if (!result.feasible) {
          figure.append(
            el(
              'p',
              'schedule-empty',
              'Adjust room capacity, available periods or class assignments to find a feasible timetable.',
            ),
          );
          return;
        }
        metrics.append(
          metric('Preference penalty', result.objective + (result.objective === 1 ? ' period' : ' periods')),
          metric('Classes scheduled', input.courses.length + ' / ' + input.courses.length),
          metric('Unused seats in booked rooms', result.seatWaste),
        );
        const table = el('table', 'problem-table schedule-timetable');
        table.append(el('caption', 'sr-only', 'Optimized room and period assignments'));
        const head = el('tr');
        head.append(el('th', '', 'Room'));
        periods.slice(0, input.slotCount).forEach((period) => head.append(el('th', '', period)));
        const h = el('thead');
        h.append(head);
        table.append(h);
        const body = el('tbody');
        for (const room of input.rooms) {
          const row = el('tr'),
            title = el('th');
          title.scope = 'row';
          title.append(
            el('strong', '', room.name),
            el('span', 'schedule-small', room.capacity + ' seats' + (room.open ? '' : ' · Closed')),
          );
          row.append(title);
          for (let slot = 0; slot < input.slotCount; slot++) {
            const cell = el('td');
            const assignment = result.assignments.find((a) => a.roomId === room.id && a.slot === slot);
            if (assignment) {
              const index = input.courses.findIndex((course) => course.id === assignment.courseId),
                course = input.courses[index];
              const card = el('div', 'schedule-class schedule-job-' + index);
              card.append(
                el('strong', '', course.name),
                el('span', '', course.teacher + ' · ' + course.cohort),
                el('span', '', course.students + ' students / ' + room.capacity + ' seats'),
                el(
                  'span',
                  'schedule-preference',
                  assignment.penalty
                    ? 'Moved ' +
                        assignment.penalty +
                        ' period' +
                        (assignment.penalty > 1 ? 's' : '') +
                        ' from preference'
                    : 'Preferred period',
                ),
              );
              cell.append(card);
            } else cell.append(el('span', 'schedule-unassigned', room.open ? 'Available' : 'Closed'));
            row.append(cell);
          }
          body.append(row);
        }
        table.append(body);
        figure.append(table);
        const mobile = el('div', 'schedule-timetable-mobile');
        for (let slot = 0; slot < input.slotCount; slot++) {
          const period = el('section', 'schedule-period-card');
          period.append(el('h3', '', periods[slot]));
          const rooms = el('div', 'schedule-period-rooms');
          for (const room of input.rooms) {
            const cell = el('div', 'schedule-mobile-room');
            cell.append(el('span', 'schedule-room-name', room.name));
            const assignment = result.assignments.find((a) => a.roomId === room.id && a.slot === slot);
            if (assignment) {
              const index = input.courses.findIndex((course) => course.id === assignment.courseId),
                course = input.courses[index];
              const card = el('div', 'schedule-class-mobile schedule-job-' + index);
              card.append(
                el('strong', '', course.name),
                el('span', '', course.teacher),
                el('span', '', course.cohort + ' · ' + course.students + ' students'),
                el(
                  'span',
                  'schedule-preference',
                  assignment.penalty
                    ? 'Moved ' + assignment.penalty + ' period' + (assignment.penalty === 1 ? '' : 's')
                    : 'Preferred period',
                ),
              );
              cell.append(card);
            } else cell.append(el('span', 'schedule-unassigned', room.open ? 'Available' : 'Closed'));
            rooms.append(cell);
          }
          period.append(rooms);
          mobile.append(period);
        }
        figure.append(mobile);
      } catch (error) {
        status.className = 'schedule-status schedule-error';
        status.textContent = error.message;
      }
    }
    frame.addEventListener('input', update);
    frame.addEventListener('change', update);
    update();
    mounted.set(root, () => {
      frame.removeEventListener('input', update);
      frame.removeEventListener('change', update);
    });
    return () => { mounted.get(root)?.(); mounted.delete(root); };
  }
  host.OptimizationViews = host.OptimizationViews || {};
  host.OptimizationViews.timetable = { mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = { solve, seed, periods, teachers };
})(typeof window !== 'undefined' ? window : globalThis);

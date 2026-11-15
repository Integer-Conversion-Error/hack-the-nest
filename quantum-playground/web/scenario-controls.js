(function (root) {
  const renderedGroups = new WeakMap();

  function controlType(definition) {
    return definition.type || (Array.isArray(definition.allowed_values) ? 'number' : '');
  }

  function decimalParts(value) {
    const [coefficient, exponent = '0'] = String(value).split('e');
    const [whole, fraction = ''] = coefficient.split('.');
    return [BigInt(whole + fraction), Number(exponent) - fraction.length];
  }

  // Compare the JSON decimal values exactly, matching the server's step validation.
  function alignsWithStep(value, minimum, step) {
    const parts = [value, minimum, step].map(decimalParts);
    const scale = Math.min(...parts.map(part => part[1]));
    const [current, start, increment] = parts.map(([digits, exponent]) => (
      digits * 10n ** BigInt(exponent - scale)
    ));
    return (current - start) % increment === 0n;
  }

  function isValidValue(definition, value) {
    const type = controlType(definition);
    if (type === 'integer' && !Number.isInteger(value)) return false;
    if (type === 'number' && !Number.isFinite(value)) return false;
    if (type === 'boolean' && typeof value !== 'boolean') return false;
    if (type === 'string' && typeof value !== 'string') return false;

    const choices = definition.allowed_values;
    if (Array.isArray(choices)) {
      return ['integer', 'number', 'boolean', 'string'].includes(type) && choices.includes(value);
    }
    if (type === 'boolean') return true;
    if (type !== 'integer' && type !== 'number') return false;

    const { minimum, maximum, step } = definition;
    return Number.isFinite(minimum) && Number.isFinite(maximum)
      && value >= minimum && value <= maximum
      && (step == null || (Number.isFinite(step) && step > 0 && alignsWithStep(value, minimum, step)));
  }

  function displayValue(value, unit) {
    const label = typeof value === 'boolean' ? (value ? 'On' : 'Off') : String(value);
    if (!unit) return label;
    return label + (unit === '%' || unit === '°' ? '' : ' ') + unit;
  }

  function render(container, definitions, applied, { onChange = () => {}, expandGroupFor } = {}) {
    const document = container.ownerDocument || root.document;
    const create = (tag, className, text) => {
      const element = document.createElement(tag);
      if (className) element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const labelFor = (text, id) => {
      const label = create('label', null, text);
      label.htmlFor = id;
      return label;
    };

    function toggle(definition, id, current, field) {
      const label = create('label', 'control-option control-toggle');
      label.htmlFor = id;
      const input = create('input');
      input.id = id;
      input.type = 'checkbox';
      input.checked = current;
      input.onchange = onChange;
      label.append(input, create('span', null, definition.label));
      field.append(label);
      return { inputs: [input], read: () => input.checked, automatic: true };
    }

    function segments(definition, id, current, field, choices) {
      const options = create('div', 'control-options');
      const inputs = choices.map((value, index) => {
        const input = create('input');
        input.type = 'radio';
        input.name = id;
        input.id = id + '-' + index;
        input.value = String(value);
        input.checked = value === current;
        input.onchange = onChange;
        const label = create('label', 'control-option');
        label.htmlFor = input.id;
        label.append(input, create('span', null, displayValue(value, definition.unit)));
        options.append(label);
        return input;
      });
      field.id = id;
      field.append(create('legend', null, definition.label), options);
      return {
        inputs,
        read: () => choices[inputs.findIndex(input => input.checked)],
        automatic: choices.length > 1,
      };
    }

    function menu(definition, id, current, field, choices) {
      const input = create('select');
      input.id = id;
      for (const value of choices) {
        const option = create('option', null, displayValue(value, definition.unit));
        option.value = String(value);
        input.append(option);
      }
      input.value = String(current);
      input.onchange = onChange;
      field.append(labelFor(definition.label, id), input);
      return { inputs: [input], read: () => choices.find(value => String(value) === input.value) };
    }

    function choiceSlider(definition, id, current, field, choices) {
      const ordered = [...choices].sort((first, second) => first - second);
      const slider = create('input', 'control-choice-slider');
      slider.id = id + '-slider';
      slider.type = 'range';
      slider.min = '0';
      slider.max = String(ordered.length - 1);
      slider.step = '1';
      slider.value = String(ordered.indexOf(current));

      const read = () => ordered[Number(slider.value)];
      const selected = create('span', 'control-choice-value');
      selected.setAttribute('aria-hidden', 'true');
      const showValue = () => {
        const text = displayValue(read(), definition.unit);
        selected.textContent = text;
        slider.setAttribute('aria-valuetext', text);
      };
      showValue();
      slider.oninput = showValue;
      slider.onchange = () => {
        showValue();
        onChange();
      };

      const heading = create('div', 'control-choice-heading');
      heading.append(labelFor(definition.label, slider.id), selected);
      const bounds = create('div', 'control-choice-bounds');
      bounds.setAttribute('aria-hidden', 'true');
      bounds.append(create('span', null, displayValue(ordered[0], definition.unit)),
        create('span', null, displayValue(ordered[ordered.length - 1], definition.unit)));
      field.append(heading, slider, bounds);
      return { inputs: [slider], read, automatic: true };
    }

    function range(definition, id, current, field) {
      const controls = create('div', 'control-range');
      const slider = create('input');
      slider.id = id + '-slider';
      slider.type = 'range';
      slider.setAttribute('aria-label', definition.label + ' slider');
      const number = create('input', 'control-value');
      number.id = id;
      number.type = 'number';
      for (const input of [slider, number]) {
        input.min = String(definition.minimum);
        input.max = String(definition.maximum);
        input.step = definition.step == null
          ? (controlType(definition) === 'integer' ? '1' : 'any')
          : String(definition.step);
        input.value = String(current);
      }

      const bounds = create('span', 'control-bounds');
      bounds.id = id + '-bounds';
      const minimum = displayValue(definition.minimum, definition.unit);
      const maximum = displayValue(definition.maximum, definition.unit);
      const validText = minimum + ' to ' + maximum;
      const step = definition.step == null ? '' : ' in steps of ' + displayValue(definition.step, definition.unit);
      number.setAttribute('aria-describedby', bounds.id);
      bounds.textContent = validText;
      const read = () => number.value.trim() ? Number(number.value) : undefined;
      const showSliderValue = () => slider.setAttribute('aria-valuetext', displayValue(Number(slider.value), definition.unit));
      showSliderValue();

      function showValidity(valid) {
        number.setAttribute('aria-invalid', String(!valid));
        bounds.textContent = valid ? validText : 'Enter ' + validText + step;
      }

      function updateFromSlider() {
        number.value = slider.value;
        showSliderValue();
        showValidity(true);
      }
      slider.oninput = updateFromSlider;
      slider.onchange = () => {
        updateFromSlider();
        onChange(definition.id);
      };
      number.oninput = () => {
        const value = read();
        const valid = isValidValue(definition, value);
        showValidity(valid);
        if (valid) {
          slider.value = String(value);
          showSliderValue();
        }
        onChange();
      };
      number.onchange = () => onChange(definition.id);

      controls.append(slider, number);
      const label = definition.unit ? definition.label + ' (' + definition.unit + ')' : definition.label;
      field.append(labelFor(label, id), controls, bounds);
      return { inputs: [slider, number], read };
    }

    const previousGroups = renderedGroups.get(container);
    const groups = new Map();
    container.replaceChildren();
    const fields = definitions.map(definition => {
      const id = 'variable-' + definition.id;
      const current = applied[definition.id];
      const choices = definition.allowed_values;
      const type = controlType(definition);
      const boolean = type === 'boolean' && (!choices || choices.length === 2);
      const numericChoices = (type === 'integer' || type === 'number') && Array.isArray(choices) && choices.length > 1;
      const smallEnum = !boolean && !numericChoices && Array.isArray(choices) && choices.length <= 4;
      const field = create(smallEnum ? 'fieldset' : 'div', 'control');
      let editor;
      if (boolean) editor = toggle(definition, id, current, field);
      else if (numericChoices) editor = choiceSlider(definition, id, current, field, choices);
      else if (smallEnum) editor = segments(definition, id, current, field, choices);
      else if (Array.isArray(choices)) editor = menu(definition, id, current, field, choices);
      else editor = range(definition, id, current, field);

      if (definition.group) {
        let group = groups.get(definition.group);
        if (!group) {
          group = create('details', 'control-group');
          group.open = previousGroups?.get(definition.group)?.open || false;
          group.append(create('summary', null, definition.group));
          groups.set(definition.group, group);
          container.append(group);
        }
        if (definition.id === expandGroupFor && !previousGroups?.has(definition.group)) group.open = true;
        group.append(field);
      } else {
        container.append(field);
      }
      return { definition, ...editor };
    });
    renderedGroups.set(container, groups);

    return {
      automatic: fields.length === 1 && !!fields[0].automatic,
      values() {
        const values = {};
        for (const { definition, read } of fields) {
          const value = read();
          if (!isValidValue(definition, value)) return null;
          values[definition.id] = value;
        }
        return values;
      },
      setDisabled(disabled) {
        for (const field of fields) {
          for (const input of field.inputs) input.disabled = disabled;
        }
      },
      displayApplied() {
        return definitions.map(({ id, label, unit }) => {
          const value = displayValue(applied[id], unit);
          return definitions.length === 1 ? value : label + ' ' + value;
        }).join(', ');
      },
    };
  }

  const api = { isValidValue, render };
  if (typeof module !== 'undefined') module.exports = api;
  else root.ScenarioControls = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

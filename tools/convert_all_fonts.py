import os
import xml.etree.ElementTree as ET

FONTS_DIR = 'fonts'
OUTPUT_DIR = 'fontsdata'

os.makedirs(OUTPUT_DIR, exist_ok=True)

def parse_path(d):
    import re
    path = []
    commands = re.findall(r'[MLZmlz][^MLZmlz]*', d)
    for cmd in commands:
        code = cmd[0]
        args = list(map(float, re.findall(r'[-+]?[0-9]*\.?[0-9]+', cmd[1:])))
        if code.upper() == 'M':
            path.append(None)
            for i in range(0, len(args), 2):
                path.append([args[i], args[i+1]])
        elif code.upper() == 'L':
            for i in range(0, len(args), 2):
                path.append([args[i], args[i+1]])
        elif code.upper() == 'Z':
            path.append(None)
    return path

def parse_svg_font(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {'svg': 'http://www.w3.org/2000/svg'}

    font = root.find('.//svg:font', ns)
    if font is None:
        return None

    font_horiz_adv_x = float(font.attrib.get('horiz-adv-x', '0'))
    glyphs = font.findall('svg:glyph', ns)

    glyph_data = {}

    for glyph in glyphs:
        unicode_char = glyph.attrib.get('unicode')
        d = glyph.attrib.get('d')
        if not unicode_char or not d or not unicode_char.isprintable():
            continue
        
        path = parse_path(d)
        horiz_adv_x = float(glyph.attrib.get('horiz-adv-x', font_horiz_adv_x))
        
        # Handle special characters (like space, quotes) with proper encoding
        char = unicode_char.replace('\\', '\\\\').replace("'", "\\'")

        # Assign path and horiz_adv_x to each character in glyph_data
        glyph_data[char] = {
            'paths': path,
            'horiz_adv_x': horiz_adv_x
        }

    return {'glyphs': glyph_data}



def format_as_ts_object(data, var_name):
    glyphs = data['glyphs']

    lines = [f"export const {var_name}: {{ glyphs: {{ [key: string]: {{ paths: Array<[number, number] | null>, horiz_adv_x: number }} }} }} = {{"]
    lines.append("  glyphs: {")

    for char, glyph in glyphs.items():
        path_str = ', '.join(
            'null' if pt is None else f'[{pt[0]:.4f}, {pt[1]:.4f}]'
            for pt in glyph['paths']
        )
        lines.append(f"    '{char}': {{")
        lines.append(f"      paths: [{path_str}],")
        lines.append(f"      horiz_adv_x: {glyph['horiz_adv_x']}")
        lines.append(f"    }},")

    lines.append("  }")
    lines.append("};")
    return '\n'.join(lines)


def filename_to_varname(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    return ''.join(c if c.isalnum() else '_' for c in base).upper() + '_FONT_DATA'

def process_all_fonts():
    for fname in os.listdir(FONTS_DIR):
        if fname.endswith('.svg'):
            input_path = os.path.join(FONTS_DIR, fname)
            font_data = parse_svg_font(input_path)
            if not font_data:
                print(f"Skipped: {fname} (no valid font)")
                continue
            var_name = filename_to_varname(fname)
            ts_content = format_as_ts_object(font_data, var_name)
            output_path = os.path.join(OUTPUT_DIR, f'{os.path.splitext(fname)[0]}.ts')
            with open(output_path, 'w') as f:
                f.write(ts_content)
            print(f"Converted: {fname} -> {output_path}")

if __name__ == '__main__':
    process_all_fonts()

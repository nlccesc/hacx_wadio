import os
os.environ["HF_HOME"] = '/mnt/cache'
import torch
from llava.model.builder import load_pretrained_model
from llava.mm_utils import (
    process_images,
    tokenizer_image_token,
    get_model_name_from_path,
)
from llava.conversation import conv_templates
from llava.utils import disable_torch_init
from llava.constants import (
    IMAGE_TOKEN_INDEX,
    DEFAULT_IMAGE_TOKEN,
    DEFAULT_IM_START_TOKEN,
    DEFAULT_IM_END_TOKEN,
    IMAGE_PLACEHOLDER,
)
import fitz
import re
from PIL import Image
from diffusers import DiffusionPipeline

# init models
os.environ["CUDA_VISIBLE_DEVICES"] = "1,2,3"
disable_torch_init()
model_path = "liuhaotian/llava-v1.5-7b"
model_name = get_model_name_from_path(model_path)
tokenizer, model, image_processor, context_len = load_pretrained_model(
    model_path=model_path,
    model_base=None,
    model_name=model_name
)

os.environ["CUDA_VISIBLE_DEVICES"] = "0"
pipe = DiffusionPipeline.from_pretrained("stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16, use_safetensors=True, variant="fp16")
pipe.to("cuda")

# boilerplate function copied from run_llava.py. Uses the globally defined `model` above.
def eval_model(images, query, batch=False, temperature=0.5, top_p=None, num_beams=1, max_new_tokens=1024):
    qs = query
    image_token_se = DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN + DEFAULT_IM_END_TOKEN
    if IMAGE_PLACEHOLDER in qs:
        if model.config.mm_use_im_start_end:
            qs = re.sub(IMAGE_PLACEHOLDER, image_token_se, qs)
        else:
            qs = re.sub(IMAGE_PLACEHOLDER, DEFAULT_IMAGE_TOKEN, qs)
    elif len(images) > 0:
        if model.config.mm_use_im_start_end:
            qs = image_token_se + "\n" + qs
        else:
            qs = DEFAULT_IMAGE_TOKEN + "\n" + qs

    if "llama-2" in model_name.lower():
        conv_mode = "llava_llama_2"
    elif "mistral" in model_name.lower():
        conv_mode = "mistral_instruct"
    elif "v1.6-34b" in model_name.lower():
        conv_mode = "chatml_direct"
    elif "v1" in model_name.lower():
        conv_mode = "llava_v1"
    elif "mpt" in model_name.lower():
        conv_mode = "mpt"
    else:
        conv_mode = "llava_v0"


    conv = conv_templates[conv_mode].copy()
    conv.append_message(conv.roles[0], qs)
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()

    out = []
    for image_file in images:
        image = Image.open(image_file).convert("RGB")
        out.append(image)
    images = out
    image_sizes = [x.size for x in images]
    if len(images) == 0:
        images_tensor = None
    else:
        images_tensor = process_images(
            images,
            image_processor,
            model.config
        ).to(model.device, dtype=torch.float16)

    input_ids = (
        tokenizer_image_token(prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt")
        .unsqueeze(0)
        .cuda()
    )
    if batch:
        input_ids = torch.cat([input_ids for _ in images]).cuda()

    with torch.inference_mode():
        output_ids = model.generate(
            input_ids,
            images=images_tensor,
            image_sizes=image_sizes,
            do_sample=True if temperature > 0 else False,
            temperature=temperature,
            top_p=top_p,
            num_beams=num_beams,
            max_new_tokens=max_new_tokens,
            use_cache=True,
        )

    outputs = tokenizer.batch_decode(output_ids, skip_special_tokens=True)
    return [i.strip() for i in outputs]





def get_image_info(image_files):
    prompt = "State important information conveyed in the image, especially factual statements and ideas, including any text. Only present information if it is factual and has educational value on its own. If there is no valueable content, do not reply with anything."
    return eval_model(image_files, prompt, batch=True)

import oai
def ask_question(text):
    result = oai.ask_question(text)
    result = result['choices'][0]['message']['content']
    return result

def split_list(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]

# returns a list of paths e.g. "pdf_images/page_1.png"
def pdf_to_img(pdf_path):
    doc = fitz.open(pdf_path)
    output_folder = "pdf_images"
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    files = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap()
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        filename = f"page_{page_num}.png"
        files.append(output_folder+'/'+filename)
        output_path = os.path.join(output_folder, filename)
        img.save(output_path)
    doc.close()
    return files


# ", ".join(SECTION_NAMES)
# TARGET_AUDIENCE
PRETEXT_CONTENT = """ Fill in text for these parts of an infographic poster against the use of drugs : {}.
Start each section with its title, followed by the content on a new line. Separate each section with 2 newlines.

An example of your response:
Header
Content of the header
Another line of content

Body
Content of the body
<End Example>
Follow this response format strictly.

This will be targeted towards {}
Below is further information on the topic. Fill in the content using this and your own knowledge.
\n\n\n"""

# NUM_IMAGES
SD_PROMPT = '''Consider {} images to supplement this content used for an infographic.
Prefer simplistic images and visualisations of content in the poster, using 2 dimensional styles whenever applicable.
Provide a textual description of these images to be used in the infographic.
Do not include text to be written on the image in this textual description.
Focus on defining features such as the subject, the artistic style et cetera.
For each image, output one line of text in point form, with its features separated by commas. Strictly only provide the visual description of the images in each line. Do not title these images in any way.
\n\nContent for the infographic:\n'''

def main( \
        resource_file='info.pdf', \
        section_names=["Header", "Body", "Section 1", "Section 2", "Footnote"], \
        target_audience="early teenagers with little to no experience with narcotics", \
        num_images=1):
    files = pdf_to_img(resource_file)
    runs = split_list(files, 10)
    print("Split into", runs)
    refs = []
    for i in range(len(runs)):
        r = runs[i]
        result = get_image_info(r)
        refs += result

    information = "\n".join(refs)
    outfile = open("reference.txt", "w")
    outfile.write(information)
    outfile.close()

    content = ask_question(PRETEXT_CONTENT.format(", ".join(section_names), target_audience) + information)
    print(content)

    # parse this into a JSON of text box contents
    output = dict()
    for chunk in content.split("\n\n"):
        lines = chunk.split("\n")
        output.update({lines[0] : '\n'.join(lines[1:])})
    import json
    outfile = open("output.json", "w")
    json.dump(output, outfile)
    outfile.close()

    p = SD_PROMPT.format(num_images) 
    print(p)
    payload = {
      "messages": [
        {
          "role": "system",
          "content": [
            {
              "type": "text",
              "text": "You are a creative artist who is highly expressive. Follow all instructions carefully and strictly."
            }
          ]
        },
        {
          "role": "user",
          "content": [
            {
              "type": "text",
              "text": p + content
            }
          ]
        }
      ],
      "temperature": 0.7,
      "top_p": 0.95,
      "max_tokens": 4096
    }

    sd_result = oai.req_api(payload)['choices'][0]['message']['content']
    lines = sd_result.split('\n')
    sd_prompt = []
    for i in lines:
        sd_prompt.append(re.sub(r'[^a-zA-Z0-9\s,.]', '', i).strip())

    outfile = open("sdxl.txt", "w")
    outfile.write('\n'.join(sd_prompt))
    outfile.close()


    # if using torch < 2.0
    # pipe.enable_xformers_memory_efficient_attention()

    for i in range(min(len(sd_prompt), num_images)):
        image = pipe(prompt=sd_prompt[i]).images[0]
        image.save(f"out_{i}.png")

main(num_images=3)
